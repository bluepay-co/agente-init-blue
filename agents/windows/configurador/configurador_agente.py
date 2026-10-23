"""
Configurador do Agente — BlueControl IT
=========================================
Interface gráfica pra instalar/reconfigurar o agente de monitoramento sem
editar C:\\BlueControl\\config.json na mão.

Arquitetura igual ao Agente Blue (agente-blue/agente.py):
  - Servidor HTTP local (só stdlib) serve a interface HTML
  - Server-Sent Events (SSE) mostram o progresso em tempo real
  - Edge/Chrome abre em modo --app (janela limpa, sem abas)

Fluxo:
  1. Busca uma máquina já cadastrada no BlueControl OU cadastra uma nova
     (endpoints /api/agente/equipamentos/* do backend, autenticados só por
     X-Agent-Token — não precisa login de usuário)
  2. Grava C:\\BlueControl\\config.json com o patrimônio resolvido
  3. Copia BlueControl_Metricas.exe (agente completo — WMI, temperatura,
     frequência, núcleos, já compilado, a máquina não precisa de Python) e
     registra o serviço Windows com "sc create". Diferente do agente leve
     antigo, este .exe já implementa o protocolo de serviço do Windows de
     verdade (via pywin32/StartServiceCtrlDispatcher), então não precisa
     de NSSM nem de nada por fora.
  4. Habilita o WinRM (Enable-PSRemoting + regra de firewall na porta 5985)
     — sem isso, Bloquear/Reiniciar pelo painel não funciona nessa máquina.

Compilar: ver compilar.sh nesta pasta (usa o mesmo Wine do Agente Blue).
"""

import ctypes
import http.server
import json
import os
import queue
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

_BASE_DIR = Path(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))))

INSTALL_DIR   = Path(r"C:\BlueControl")
CONFIG_PATH   = INSTALL_DIR / "config.json"
AGENTE_EXE    = "BlueControl_Metricas.exe"
SERVICE_NAME  = "BlueControlMetricas"
SERVICE_DISP  = "BlueControl IT - Agente de Métricas"
WINRM_PORTA   = "5985"
REMOTE_EXE    = "BlueControl_Remote.exe"
REMOTE_TASK   = "BlueControlRemote"
LEGADO_SERVICE = "BlueControlLite"

DEFAULT_URL_API   = "http://192.168.0.17:8550"
# Sem padrão no código: um token conhecido na fonte vale tanto quanto nenhum.
DEFAULT_TOKEN     = os.environ.get("BLUECONTROL_AGENT_TOKEN", "")
DEFAULT_INTERVALO = 300

# ══════════════════════════════════════════════
#  ADMIN
# ══════════════════════════════════════════════

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate_and_restart() -> None:
    params = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    sys.exit(0)


# ══════════════════════════════════════════════
#  SSE — FILA DE EVENTOS
# ══════════════════════════════════════════════
_sse_queues: list = []
_sse_lock = threading.Lock()


def _broadcast(event: dict):
    with _sse_lock:
        for q in _sse_queues:
            q.put(event)


def _log(msg: str, tipo: str = "muted"):
    _broadcast({"type": "log", "msg": msg, "tipo": tipo})


def _progresso(pct: int, texto: str, ok: bool = False, erro: bool = False):
    _broadcast({"type": "progresso", "pct": pct, "texto": texto, "ok": ok, "erro": erro})


# ══════════════════════════════════════════════
#  HTTP HELPERS (sem depender de `requests`)
# ══════════════════════════════════════════════

def _http_json(method: str, url: str, token: str, body: Optional[dict] = None, timeout: int = 10):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Agent-Token", token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", str(e))
        except Exception:
            detail = str(e)
        return e.code, {"detail": detail}
    except urllib.error.URLError as e:
        return 0, {"detail": f"Não foi possível conectar em {url} ({e.reason})"}


# ══════════════════════════════════════════════
#  INSTALAÇÃO (roda em thread separada)
# ══════════════════════════════════════════════

def _run_cmd(cmd: list, label: str) -> tuple:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok = r.returncode == 0
        if not ok:
            _log(f"{label}: {(r.stderr or r.stdout or '').strip()[:200]}", "warn")
        return ok, r.stdout
    except Exception as e:
        _log(f"{label}: {e}", "err")
        return False, ""


def _instalar_servico(agente_path: Path):
    """Registra o serviço Windows com "sc create" direto — funciona porque
    BlueControl_Metricas.exe já implementa o protocolo de serviço do Windows
    de verdade (StartServiceCtrlDispatcher via pywin32), diferente do agente
    leve antigo, que só rodava um loop comum e o Windows derrubava sozinho."""
    subprocess.run(["sc", "stop", SERVICE_NAME], capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(1)
    subprocess.run(["sc", "delete", SERVICE_NAME], capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(1)

    ok, _ = _run_cmd(
        ["sc", "create", SERVICE_NAME, "binPath=", f'"{agente_path}"',
         "start=", "auto", "DisplayName=", SERVICE_DISP, "obj=", "LocalSystem"],
        "Criar serviço",
    )
    if not ok:
        return False
    subprocess.run(["sc", "description", SERVICE_NAME, SERVICE_DISP], capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _run_cmd(["sc", "start", SERVICE_NAME], "Iniciar serviço")

    # "start" só confirma que o pedido foi aceito, não que o serviço continuou
    # de pé — confere de verdade com uma pausa curta.
    time.sleep(2)
    try:
        r = subprocess.run(["sc", "query", SERVICE_NAME], capture_output=True, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "RUNNING" in r.stdout
    except Exception:
        return False


def _habilitar_winrm():
    """Liga o WinRM e libera a porta 5985 no firewall — sem isso, o painel
    não consegue Bloquear/Reiniciar essa máquina remotamente (usa WinRM, não
    o agente de monitoramento)."""
    _run_cmd(["powershell", "-NoProfile", "-Command",
              "Enable-PSRemoting -Force -SkipNetworkProfileCheck"],
             "Habilitar WinRM")
    _run_cmd(["powershell", "-NoProfile", "-Command", "Set-Service WinRM -StartupType Automatic"],
             "Serviço WinRM automático")
    subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule",
                    "name=WinRM BlueControl"], capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # Libera ping (ICMPv4 echo) — o Windows 11 bloqueia por padrão, e o NOC usa
    # ping pra mostrar online/offline.
    subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule",
                    "name=Ping BlueControl"], capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _run_cmd(["netsh", "advfirewall", "firewall", "add", "rule", "name=Ping BlueControl",
              "protocol=icmpv4:8,any", "dir=in", "action=allow"],
             "Liberar ping no firewall")
    ok, _ = _run_cmd(
        ["netsh", "advfirewall", "firewall", "add", "rule", "name=WinRM BlueControl",
         "protocol=TCP", "dir=in", f"localport={WINRM_PORTA}", "action=allow"],
        "Liberar porta no firewall",
    )
    return ok


def _instalar_acesso_remoto(origem: Path) -> bool:
    """Instala o agente de acesso remoto como TAREFA AGENDADA no logon, não
    como serviço: serviço roda na sessão 0, que não enxerga a tela nem o
    mouse/teclado do usuário. A tarefa roda como o grupo Usuários (qualquer
    pessoa que logar) com privilégio mais alto, e é disparada na hora pra
    sessão já aberta — não precisa deslogar."""
    destino = INSTALL_DIR / REMOTE_EXE
    # Mata instância antiga antes de sobrescrever o .exe (arquivo em uso)
    subprocess.run(["taskkill", "/F", "/IM", REMOTE_EXE], capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(1)
    try:
        shutil.copy(str(origem), str(destino))
    except Exception as e:
        _log(f"Copiar agente remoto: {e}", "err")
        return False

    ps = f"""
$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName '{REMOTE_TASK}' -Confirm:$false -ErrorAction SilentlyContinue
$acao = New-ScheduledTaskAction -Execute '{destino}' -WorkingDirectory '{INSTALL_DIR}'
$gatilho = New-ScheduledTaskTrigger -AtLogOn
# S-1-5-32-545 = grupo Usuários local (SID independe do idioma do Windows)
$principal = New-ScheduledTaskPrincipal -GroupId 'S-1-5-32-545' -RunLevel Highest
$cfg = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName '{REMOTE_TASK}' -Action $acao -Trigger $gatilho -Principal $principal -Settings $cfg | Out-Null
Start-ScheduledTask -TaskName '{REMOTE_TASK}'
"""
    ok, _ = _run_cmd(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                     "Registrar tarefa do acesso remoto")
    if not ok:
        return False
    time.sleep(4)
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {REMOTE_EXE}"], capture_output=True, text=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return REMOTE_EXE.lower() in r.stdout.lower()


def _parar_agente_antigo():
    """Máquina já cadastrada costuma ter o agente rodando e segurando o .exe.
    "sc stop" é assíncrono (e o .exe onefile tem processo filho), então espera
    o STOPPED de verdade e mata o que sobrar pelo nome."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(["sc", "stop", SERVICE_NAME], capture_output=True, creationflags=flags)
    for _ in range(15):
        r = subprocess.run(["sc", "query", SERVICE_NAME], capture_output=True, text=True, creationflags=flags)
        if r.returncode != 0 or "STOPPED" in r.stdout:
            break
        time.sleep(1)
    subprocess.run(["taskkill", "/F", "/T", "/IM", AGENTE_EXE], capture_output=True, creationflags=flags)
    # Agente leve antigo (instalar_agente_lite.bat) — se ficar de pé, a máquina
    # manda métricas por dois agentes ao mesmo tempo.
    if subprocess.run(["sc", "query", LEGADO_SERVICE], capture_output=True, creationflags=flags).returncode == 0:
        subprocess.run(["sc", "stop", LEGADO_SERVICE], capture_output=True, creationflags=flags)
        subprocess.run(["sc", "delete", LEGADO_SERVICE], capture_output=True, creationflags=flags)
        subprocess.run(["taskkill", "/F", "/T", "/IM", "BlueControl_Lite.exe"], capture_output=True, creationflags=flags)
        _log(f"Serviço antigo {LEGADO_SERVICE} removido", "info")
    time.sleep(1)


def _substituir_exe_travado(origem: Path, destino: Path) -> bool:
    _log("Parando agente antigo (se houver)…", "muted")
    _parar_agente_antigo()
    # Sobras do "renomeia e copia" de instalações anteriores
    for velho in INSTALL_DIR.glob(f"{destino.stem}.old*"):
        try:
            velho.unlink()
        except OSError:
            pass
    for _ in range(5):
        try:
            shutil.copy(str(origem), str(destino))
            return True
        except PermissionError:
            time.sleep(2)
    # Último recurso: o Windows deixa RENOMEAR um .exe em uso, só não sobrescrever.
    try:
        antigo = destino.with_suffix(f".old{int(time.time())}")
        destino.rename(antigo)
        shutil.copy(str(origem), str(destino))
        _log(f"Agente antigo ainda em uso — renomeado para {antigo.name}", "warn")
        return True
    except Exception as e:
        _log(f"Não consegui substituir {destino} (arquivo em uso): {e}", "err")
        return False


def executar_instalacao(params: dict):
    try:
        patrimonio = params["patrimonio"]
        url_api    = params["url_api"].rstrip("/")
        token      = params["token_agente"]
        intervalo  = int(params.get("intervalo_segundos") or DEFAULT_INTERVALO)

        _progresso(10, "Criando C:\\BlueControl…")
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        _log(f"Pasta {INSTALL_DIR} pronta", "ok")

        _progresso(25, "Gravando config.json…")
        config = {
            "patrimonio": patrimonio,
            "url_api": url_api,
            "token_agente": token,
            "intervalo_segundos": intervalo,
            # Nomes que o BlueControl_Metricas.exe lê — mantém os dois pra
            # funcionar com agentes antigos e novos
            "api_urls": [url_api],
            "agent_secret": token,
        }
        CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        _log(f"config.json gravado — patrimônio {patrimonio}", "ok")

        _progresso(40, "Copiando o agente…")
        # Embutido no .exe do configurador (--add-data) ou, em modo dev, ao
        # lado dele / uma pasta acima (layout do repositório).
        agente_origem = None
        for candidato in (_BASE_DIR / AGENTE_EXE, _BASE_DIR / ".." / AGENTE_EXE,
                          _BASE_DIR / ".." / "dist" / AGENTE_EXE):
            if candidato.exists():
                agente_origem = candidato
                break
        if not agente_origem:
            _log(f"{AGENTE_EXE} não encontrado — reinstale usando o .exe completo do configurador", "err")
            _progresso(100, "Arquivo necessário não encontrado", erro=True)
            return

        agente_destino = INSTALL_DIR / AGENTE_EXE
        if not _substituir_exe_travado(agente_origem, agente_destino):
            _progresso(100, "Não consegui substituir o agente antigo", erro=True)
            return
        _log("Agente copiado — a máquina não precisa de Python instalado", "ok")

        _progresso(60, "Instalando serviço Windows...")
        servico_ok = _instalar_servico(agente_destino)
        if servico_ok:
            _log("Serviço de métricas instalado e rodando", "ok")
        else:
            _log("Serviço foi criado mas não ficou rodando — confira C:\\BlueControl\\metricas.log", "err")

        _progresso(72, "Instalando acesso remoto...")
        remote_origem = next((c for c in (_BASE_DIR / REMOTE_EXE, _BASE_DIR / ".." / "dist" / REMOTE_EXE)
                              if c.exists()), None)
        if not remote_origem:
            _log(f"{REMOTE_EXE} não embutido neste configurador — acesso remoto não instalado", "warn")
        elif _instalar_acesso_remoto(remote_origem):
            _log("Acesso remoto instalado e conectando (roda no logon de qualquer usuário)", "ok")
        else:
            _log("Acesso remoto registrado mas não iniciou — confira C:\\BlueControl\\remote.log", "warn")

        _progresso(85, "Habilitando WinRM (bloqueio/reinício remoto)...")
        if _habilitar_winrm():
            _log("WinRM habilitado — Bloquear/Reiniciar pelo painel já funciona nessa máquina", "ok")
        else:
            _log("Não consegui habilitar o WinRM automaticamente — configure manualmente depois", "warn")

        if servico_ok:
            _progresso(100, "Instalação concluída!", ok=True)
        else:
            _log(f"Rode direto pra ver o erro do agente: {agente_destino}", "info")
            _progresso(100, "Serviço de métricas não ficou de pé — veja o log acima", erro=True)

    except Exception as e:
        _log(f"Erro inesperado: {e}", "err")
        _progresso(100, "Falha na instalação", erro=True)


# ══════════════════════════════════════════════
#  SERVIDOR HTTP
# ══════════════════════════════════════════════

class ConfiguradorHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_BASE_DIR), **kwargs)

    def log_message(self, format, *args):
        pass

    def _json_response(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # ── GET ──────────────────────────────────
    def do_GET(self):
        if self.path == "/api/stream":
            self._handle_sse()
        elif self.path == "/api/status":
            self._status()
        else:
            super().do_GET()

    def _status(self):
        existe = CONFIG_PATH.exists()
        cfg = {}
        if existe:
            try:
                cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        servico = "desconhecido"
        try:
            r = subprocess.run(["sc", "query", SERVICE_NAME], capture_output=True, text=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if "RUNNING" in r.stdout:
                servico = "rodando"
            elif "STOPPED" in r.stdout:
                servico = "parado"
            elif r.returncode != 0:
                servico = "nao_instalado"
        except Exception:
            pass
        self._json_response({
            "existe": existe,
            "patrimonio": cfg.get("patrimonio", ""),
            "url_api": cfg.get("url_api") or cfg.get("api_url") or (cfg.get("api_urls") or [DEFAULT_URL_API])[0],
            "token_agente": cfg.get("token_agente") or cfg.get("agent_secret") or DEFAULT_TOKEN,
            "intervalo_segundos": cfg.get("intervalo_segundos", DEFAULT_INTERVALO),
            "servico": servico,
        })

    def _handle_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q: queue.Queue = queue.Queue()
        with _sse_lock:
            _sse_queues.append(q)
        try:
            while True:
                try:
                    event = q.get(timeout=20)
                    self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _sse_lock:
                _sse_queues.remove(q)

    # ── POST ─────────────────────────────────
    def do_POST(self):
        if self.path == "/api/buscar":
            body = self._read_json()
            status, data = _http_json(
                "GET",
                f"{body['url_api'].rstrip('/')}/api/agente/equipamentos/buscar?q={urllib.parse.quote(body.get('q',''))}",
                body["token_agente"],
            )
            self._json_response(data, status or 502)

        elif self.path == "/api/cadastrar":
            body = self._read_json()
            url_api = body.pop("url_api")
            token = body.pop("token_agente")
            status, data = _http_json("POST", f"{url_api.rstrip('/')}/api/agente/equipamentos", token, body)
            self._json_response(data, status or 502)

        elif self.path == "/api/instalar":
            params = self._read_json()
            threading.Thread(target=executar_instalacao, args=(params,), daemon=True).start()
            self._json_response({"ok": True})

        else:
            self.send_error(404)


class _ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


_EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _open_app_window(url: str):
    for path in _EDGE_PATHS + _CHROME_PATHS:
        if Path(path).exists():
            subprocess.Popen([
                path, f"--app={url}",
                "--window-size=460,760",
                "--window-position=100,40",
                "--disable-extensions",
                "--no-first-run",
            ])
            return
    webbrowser.open(url)


def main() -> None:
    if not is_admin():
        elevate_and_restart()
        return

    port = _find_free_port()
    server = _ThreadedServer(("127.0.0.1", port), ConfiguradorHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{port}/index.html"
    _open_app_window(url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
