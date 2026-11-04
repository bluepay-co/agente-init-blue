# ============================================================
# BlueControl Remote — agente de acesso remoto
# Captura a tela e codifica em H.264 de baixa latência (libx264,
# ultrafast/zerolatency) — o navegador decodifica nativamente via
# WebCodecs (VideoDecoder), com aceleração de hardware quando disponível.
# Diferente do antigo "print + JPEG a cada frame", aqui há compressão
# entre quadros: uma tela parada custa poucos bytes por segundo.
# Recebe eventos de mouse/teclado do servidor e executa com pyautogui.
# Só captura quando há um operador assistindo (evento "viewer" do servidor).
#
# Compilar:
#   pyinstaller --onefile --noconsole --collect-all av --name BlueControl_Remote BlueControl_Remote.py
# ============================================================

import os
import sys
import json
import time
import ctypes
import zlib
import asyncio
import threading
import concurrent.futures
import socket

import av
import numpy as np
import mss
import pyautogui
import websockets
from PIL import Image, ImageDraw

# ── configuração ──────────────────────────────────────────────
# agent_secret e api_urls vêm do C:\BlueControl\config.json (mesmo arquivo que o
# BlueControl_Metricas usa, escrito pelo instalador em cada máquina — nunca commitado).
# Variáveis de ambiente têm prioridade, só pra permitir teste local (dev).
def _load_agent_config() -> dict:
    try:
        with open(r"C:\BlueControl\config.json", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}

_cfg = _load_agent_config()
# "url_api"/"token_agente" são os nomes gravados pelo ConfiguradorAgente antigo
_cfg_api_urls = _cfg.get("api_urls") or ([_cfg["url_api"]] if _cfg.get("url_api") else [])
_cfg_ws = _cfg_api_urls[0].replace("http://", "ws://").replace("https://", "wss://") if _cfg_api_urls else ""

SERVER_WS = os.getenv("BC_SERVER_WS", _cfg_ws)
AGENT_SECRET = os.getenv("AGENT_SECRET", _cfg.get("agent_secret") or _cfg.get("token_agente", ""))
FPS = int(os.getenv("BC_REMOTE_FPS", "20"))
MAX_WIDTH = int(os.getenv("BC_REMOTE_MAX_WIDTH", "1024"))
CRF = os.getenv("BC_REMOTE_CRF", "28")   # qualidade H.264: menor = melhor e maior
LOG_PATH = r"C:\BlueControl\remote.log"

# Tipos de mensagem binária enviados ao viewer (1º byte de cada frame WS)
MSG_DELTA = 0  # quadro P (depende do anterior)
MSG_KEY = 1    # quadro-chave (keyframe/IDR) — decodificável sozinho
MSG_INIT = 2   # config inicial (JSON: width, height, codec) p/ configurar o VideoDecoder

def log(msg: str):
    linha = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        # Mantém o log pequeno (~200KB)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 200_000:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except Exception:
        pass
    try:
        if sys.stdout:
            print(linha)
    except Exception:
        pass

# Patrimônio: config.json (mesmo do BlueControl_Metricas) → patrimonio.txt → hostname
def get_patrimonio() -> str:
    try:
        with open(r"C:\BlueControl\config.json", encoding="utf-8") as f:
            pat = str(json.load(f).get("patrimonio", "")).strip()
        if pat:
            return pat
    except Exception:
        pass
    path = r"C:\BlueControl\patrimonio.txt"
    try:
        if os.path.exists(path):
            pat = open(path).read().strip()
            if pat:
                return pat
    except Exception:
        pass
    return socket.gethostname()

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

# Resolução real da tela — o frame enviado ao viewer pode estar reduzido
# (downscale em capture_pil), então as coordenadas chegam como fração (0..1)
# e precisam ser convertidas para o pixel real daqui.
_SCREEN_W, _SCREEN_H = pyautogui.size()

def _real_xy(evt: dict):
    return evt["nx"] * _SCREEN_W, evt["ny"] * _SCREEN_H

# ── ponteiro do mouse ──────────────────────────────────────────
# BitBlt (usado pelo mss) não captura o cursor do sistema, então desenhamos
# um ponteiro sintético na posição real do mouse antes de codificar o frame.
class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

def _cursor_pos():
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

def _draw_cursor(pil_img: Image.Image, x: float, y: float):
    draw = ImageDraw.Draw(pil_img)
    pts = [(x, y), (x, y + 17), (x + 4, y + 13), (x + 7, y + 20),
           (x + 10, y + 19), (x + 7, y + 12), (x + 12, y + 12)]
    draw.polygon(pts, fill=(255, 255, 255), outline=(0, 0, 0))

# ── codificador H.264 ──────────────────────────────────────────
def _even(n: int) -> int:
    return n - (n % 2)  # libx264 exige dimensões pares (subamostragem yuv420p)

def _parse_codec_string(idr_bytes: bytes) -> str:
    """Lê o NAL de SPS do keyframe pra montar a string de codec real
    (profile/constraints/level) que o WebCodecs do navegador espera."""
    i, n = 0, len(idr_bytes)
    while i < n - 4:
        if idr_bytes[i:i + 4] == b"\x00\x00\x00\x01":
            start = i + 4
            if start < n and (idr_bytes[start] & 0x1F) == 7 and start + 4 <= n:
                p, c, l = idr_bytes[start + 1:start + 4]
                return f"avc1.{p:02X}{c:02X}{l:02X}"
            i = start
        else:
            i += 1
    return "avc1.42E01F"  # fallback razoável (baseline)

class H264Encoder:
    """Codifica frames RGB em H.264 Annex-B (baseline, ultrafast/zerolatency).
    Compressão entre quadros: uma tela parada custa poucos bytes por frame,
    bem diferente do antigo 'print + JPEG' que reenviava a imagem inteira."""

    def __init__(self, width: int, height: int, fps: int):
        self.width = max(2, _even(width))
        self.height = max(2, _even(height))
        self.codec_str = None
        self.init_sent = False
        self._pts = 0
        self._cc = av.CodecContext.create("libx264", "w")
        self._cc.width = self.width
        self._cc.height = self.height
        self._cc.pix_fmt = "yuv420p"
        self._cc.framerate = fps
        self._cc.options = {
            "preset": "ultrafast",
            "tune": "zerolatency",
            "crf": CRF,
            "x264-params": f"repeat-headers=1:keyint={fps * 2}:scenecut=0",
        }
        self._cc.open()

    def encode(self, pil_img: Image.Image):
        """Retorna lista de (is_keyframe: bool, dados: bytes) para o frame dado."""
        if pil_img.size != (self.width, self.height):
            pil_img = pil_img.resize((self.width, self.height), Image.BILINEAR)
        arr = np.asarray(pil_img)
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24").reformat(format="yuv420p")
        frame.pts = self._pts
        self._pts += 1
        out = []
        for pkt in self._cc.encode(frame):
            data = bytes(pkt)
            if pkt.is_keyframe and self.codec_str is None:
                self.codec_str = _parse_codec_string(data)
            out.append((pkt.is_keyframe, data))
        return out

    def close(self):
        try:
            for _ in self._cc.encode(None):  # descarrega buffers internos do encoder
                pass
        except Exception:
            pass

# ── captura de tela ──────────────────────────────────────────
# mss tem afinidade de thread no Windows: a instância precisa ser criada e
# usada na MESMA thread. Usamos um pool de 1 thread com instância thread-local,
# recriada quando a captura falha (tela bloqueada, troca de resolução etc).
# A codificação H.264 roda na mesma thread/pool — sem custo extra relevante,
# já que evita gerenciar um segundo pool só pra isso.
_capture_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_tls = threading.local()
_last_sig = None  # (crc32 do frame anterior, posição do cursor) — só usado na thread de captura

def capture_pil():
    """Retorna um PIL.Image já redimensionado e com o cursor desenhado, ou
    None se a tela não mudou desde o último frame (economiza CPU quando o
    usuário está só olhando, parado)."""
    global _last_sig
    sct = getattr(_tls, "sct", None)
    if sct is None:
        sct = mss.mss()
        _tls.sct = sct
    try:
        monitor = sct.monitors[1]  # monitor primário
        img = sct.grab(monitor)
    except Exception:
        try:
            sct.close()
        except Exception:
            pass
        _tls.sct = None
        raise

    cx, cy = _cursor_pos()
    sig = (zlib.crc32(img.raw), cx, cy)
    if sig == _last_sig:
        return None
    _last_sig = sig

    pil = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
    w, h = pil.size
    scale = MAX_WIDTH / w if w > MAX_WIDTH else 1.0
    if scale != 1.0:
        pil = pil.resize((MAX_WIDTH, int(h * scale)), Image.BILINEAR)

    try:
        _draw_cursor(pil, (cx - monitor["left"]) * scale, (cy - monitor["top"]) * scale)
    except Exception:
        pass

    return pil

# ── executor de eventos ──────────────────────────────────────
def handle_event(evt: dict):
    t = evt.get("type")
    if t == "mousemove":
        x, y = _real_xy(evt)
        pyautogui.moveTo(x, y)
    elif t == "mousedown":
        x, y = _real_xy(evt)
        btn = evt.get("button", "left")
        pyautogui.mouseDown(x=x, y=y, button=btn)
    elif t == "mouseup":
        x, y = _real_xy(evt)
        btn = evt.get("button", "left")
        pyautogui.mouseUp(x=x, y=y, button=btn)
    elif t == "click":
        x, y = _real_xy(evt)
        btn = evt.get("button", "left")
        pyautogui.click(x=x, y=y, button=btn)
    elif t == "dblclick":
        x, y = _real_xy(evt)
        pyautogui.doubleClick(x=x, y=y)
    elif t == "wheel":
        dy = evt.get("deltaY", 0)
        clicks = int(dy / 120)
        pyautogui.scroll(-clicks)
    elif t == "keydown":
        key = evt.get("key", "")
        _press_key(key)
    elif t == "paste":
        text = evt.get("text", "")
        pyautogui.typewrite(text, interval=0.02)

def _press_key(key: str):
    mapping = {
        "Enter": "enter", "Backspace": "backspace", "Tab": "tab",
        "Escape": "esc", "Delete": "delete", "ArrowUp": "up",
        "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
        "Home": "home", "End": "end", "PageUp": "pageup",
        "PageDown": "pagedown", "F1": "f1", "F2": "f2", "F3": "f3",
        "F4": "f4", "F5": "f5", "F6": "f6", "F7": "f7", "F8": "f8",
        "F9": "f9", "F10": "f10", "F11": "f11", "F12": "f12",
        "Control": "ctrl", "Alt": "alt", "Shift": "shift",
        " ": "space",
    }
    # Combinação de teclas (ex.: "ctrl+shift+escape") — usada pelo botão de
    # atalho do painel. Ctrl+Alt+Del real (SAS) é bloqueado pelo Windows para
    # qualquer processo comum, por segurança — não é simulável por aqui.
    if "+" in key:
        partes = [p.strip().lower() for p in key.split("+") if p.strip()]
        try:
            pyautogui.hotkey(*partes)
        except Exception:
            pass
        return
    if key in mapping:
        pyautogui.press(mapping[key])
    elif len(key) == 1:
        pyautogui.typewrite(key, interval=0)

# ── loop principal ────────────────────────────────────────────
async def run():
    patrimonio = get_patrimonio()
    uri = f"{SERVER_WS}/ws/remote/{patrimonio}?secret={AGENT_SECRET}"
    interval = 1.0 / FPS

    while True:
        encoder = None
        try:
            log(f"Conectando a {uri}")
            async with websockets.connect(uri, max_size=10 * 1024 * 1024) as ws:
                log(f"Conectado — patrimônio={patrimonio} fps={FPS}")
                viewer_on = False
                falhas_captura = 0
                last = 0.0
                loop = asyncio.get_event_loop()
                while True:
                    # Sem viewer: só aguarda eventos (nenhuma captura).
                    # Com viewer: espera até a hora do próximo frame.
                    if viewer_on:
                        timeout = max(0.001, interval - (time.monotonic() - last))
                    else:
                        timeout = 5.0
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                        evt = json.loads(raw)
                        if evt.get("type") == "viewer":
                            viewer_on = bool(evt.get("connected"))
                            log(f"Viewer {'conectado — iniciando captura' if viewer_on else 'desconectado — captura pausada'}")
                            last = 0.0
                            global _last_sig
                            _last_sig = None  # força o envio do primeiro frame ao reconectar
                            if not viewer_on and encoder:
                                encoder.close()
                                encoder = None
                            continue
                        threading.Thread(
                            target=handle_event, args=(evt,), daemon=True
                        ).start()
                    except asyncio.TimeoutError:
                        pass

                    if not viewer_on:
                        continue

                    # captura + codifica no ritmo do FPS
                    now = time.monotonic()
                    if now - last < interval:
                        continue
                    last = now

                    try:
                        pil = await loop.run_in_executor(_capture_pool, capture_pil)
                        falhas_captura = 0
                    except Exception as e:
                        # Tela bloqueada/protegida: pula o frame, mantém a conexão
                        falhas_captura += 1
                        if falhas_captura == 1 or falhas_captura % 30 == 0:
                            log(f"Falha na captura ({e}) — tentando novamente")
                        await asyncio.sleep(2)
                        continue

                    if pil is None:
                        continue  # tela igual à do frame anterior — nada a codificar/enviar

                    w, h = _even(pil.size[0]), _even(pil.size[1])
                    if encoder is None or encoder.width != w or encoder.height != h:
                        if encoder:
                            encoder.close()
                        encoder = H264Encoder(pil.size[0], pil.size[1], FPS)
                        log(f"Encoder H.264 iniciado: {encoder.width}x{encoder.height}")

                    pkts = await loop.run_in_executor(_capture_pool, encoder.encode, pil)
                    if pkts and not encoder.init_sent and encoder.codec_str:
                        init = json.dumps({
                            "width": encoder.width, "height": encoder.height,
                            "codec": encoder.codec_str,
                        }).encode("utf-8")
                        await ws.send(bytes([MSG_INIT]) + init)
                        encoder.init_sent = True
                    for is_key, data in pkts:
                        await ws.send(bytes([MSG_KEY if is_key else MSG_DELTA]) + data)
        except (websockets.ConnectionClosed, OSError) as e:
            log(f"Desconectado: {e} — reconectando em 5s")
            if encoder:
                encoder.close()
            await asyncio.sleep(5)
        except Exception as e:
            log(f"Erro inesperado: {e} — reconectando em 10s")
            if encoder:
                encoder.close()
            await asyncio.sleep(10)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception:
        import traceback
        try:
            with open(r"C:\BlueControl\remote_error.log", "a", encoding="utf-8") as f:
                f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] CRASH:\n")
                f.write(traceback.format_exc())
        except Exception:
            pass
        raise
