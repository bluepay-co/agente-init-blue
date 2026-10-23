"""
BlueControl IT — Agente de Métricas Mac
=========================================
Coleta métricas (5 min), inventário (60 min), processa comandos (60 s).
Bloquear / desbloquear via dscl + pkill loginwindow.

Instalar dependências (Python 3.9+ / pip3):
  pip3 install psutil requests

Caminho: /usr/local/bluecontrol/agente_metricas_mac.py
Log:     /usr/local/bluecontrol/metricas.log
Config:  /usr/local/bluecontrol/config.json

LaunchDaemon plist: /Library/LaunchDaemons/com.bluecontrol.metricas.plist
"""

# Precisa vir antes de tudo: adia a avaliação das anotações de tipo, para o
# agente rodar no Python 3.9 do macOS (a sintaxe "int | None" só existe no 3.10+).
from __future__ import annotations

import os, sys, json, time, socket, logging, re, subprocess, hashlib, signal
import requests, psutil
from pathlib import Path

# ─── Caminhos ──────────────────────────────────────────────────────────────
BASE_DIR      = Path("/usr/local/bluecontrol")
CONFIG_PATH   = BASE_DIR / "config.json"
LOG_PATH      = BASE_DIR / "metricas.log"
AGENTE_PATH   = BASE_DIR / "agente_metricas_mac.py"
BLOQUEADO_TXT = BASE_DIR / "usuario_bloqueado.txt"

BASE_DIR.mkdir(parents=True, exist_ok=True)

# ─── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    filename=str(LOG_PATH), level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("bluecontrol_mac")
_con = logging.StreamHandler(sys.stdout)
_con.setLevel(logging.INFO)
_con.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
log.addHandler(_con)

# ─── Usuários protegidos (nunca bloquear) ──────────────────────────────────
USUARIOS_PROTEGIDOS = {
    "administrador", "administrator", "admin", "root", "bluecontrol",
    "bluepay", "daemon", "guest", "nobody", "loginwindow", "_mbsetupuser",
    "_spotlight", "_mdnsresponder", "_networkd",
}

# ─── Helpers shell ─────────────────────────────────────────────────────────
def _sh(cmd: list, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def _sh_str(cmd: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)

def _out(cmd: list, timeout: int = 20) -> str:
    try:
        return _sh(cmd, timeout).stdout.strip()
    except Exception:
        return ""

def _out_s(cmd: str, timeout: int = 20) -> str:
    try:
        return _sh_str(cmd, timeout).stdout.strip()
    except Exception:
        return ""

def _out_int(cmd: list, timeout: int = 20) -> int | None:
    v = _out(cmd, timeout)
    try:
        return int(v)
    except Exception:
        return None

# ─── Config ────────────────────────────────────────────────────────────────
def carregar_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config não encontrado: {CONFIG_PATH}")
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)

# ─── API ───────────────────────────────────────────────────────────────────
def _headers(config: dict) -> dict:
    # Sem padrão no código: um token conhecido na fonte vale tanto quanto nenhum.
    token = config.get("agent_secret") or config.get("token_agente")
    if not token:
        raise SystemExit("[config] 'agent_secret' ausente em " + str(CONFIG_PATH))
    return {
        "X-Agent-Token": token,
        "Content-Type": "application/json",
    }

def _api_urls(config: dict) -> list:
    return config.get("api_urls") or [config.get("api_url", "http://192.168.10.11:8550")]

# ─── Hash do agente ────────────────────────────────────────────────────────
_AGENTE_HASH: str = ""

def _hash_arquivo(path: Path) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""

# ─── Temperatura CPU ────────────────────────────────────────────────────────
def _cpu_temp() -> float | None:
    try:
        out = _out(["sudo", "powermetrics", "--samplers", "smc", "-n1", "-i500"], timeout=5)
        m = re.search(r"CPU die temperature:\s*([\d.]+)", out)
        if m:
            return float(m.group(1))
        m = re.search(r"SMC CPU[^:]*?:\s*([\d.]+)", out)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return None

# ─── Usuário no console ─────────────────────────────────────────────────────
def _usuario_console() -> str | None:
    try:
        u = _out(["stat", "-f", "%Su", "/dev/console"])
        return u if u and u not in ("", "root", "loginwindow") else None
    except Exception:
        return None

# ─── MÉTRICAS (a cada 5 min) ────────────────────────────────────────────────
def coletar_metricas() -> dict:
    psutil.cpu_percent(interval=None)
    try:
        psutil.cpu_percent(percpu=True, interval=None)
    except Exception:
        pass
    net_t0 = psutil.net_io_counters()
    try:
        disk_t0 = psutil.disk_io_counters()
    except Exception:
        disk_t0 = None

    time.sleep(1)

    cpu_percent = round(psutil.cpu_percent(interval=None), 1)
    try:
        cores_percent = [round(c, 1) for c in psutil.cpu_percent(percpu=True, interval=None)]
    except Exception:
        cores_percent = []

    try:
        freq = psutil.cpu_freq()
        cpu_freq_mhz     = round(freq.current, 0) if freq else None
        cpu_freq_max_mhz = round(freq.max,     0) if (freq and freq.max) else None
    except Exception:
        cpu_freq_mhz = cpu_freq_max_mhz = None

    ram = psutil.virtual_memory()

    try:
        disco          = psutil.disk_usage("/")
        disco_percent  = round(disco.percent, 1)
        disco_total_gb = round(disco.total / (1024**3), 1)
    except Exception:
        disco_percent = disco_total_gb = None

    try:
        disk_t1 = psutil.disk_io_counters()
        if disk_t0 and disk_t1:
            disco_io_read_mbs  = round((disk_t1.read_bytes  - disk_t0.read_bytes)  / (1024*1024), 2)
            disco_io_write_mbs = round((disk_t1.write_bytes - disk_t0.write_bytes) / (1024*1024), 2)
        else:
            disco_io_read_mbs = disco_io_write_mbs = None
    except Exception:
        disco_io_read_mbs = disco_io_write_mbs = None

    try:
        net_t1 = psutil.net_io_counters()
        net_recv_mbs = round((net_t1.bytes_recv - net_t0.bytes_recv) / (1024*1024), 3)
        net_sent_mbs = round((net_t1.bytes_sent - net_t0.bytes_sent) / (1024*1024), 3)
    except Exception:
        net_recv_mbs = net_sent_mbs = None

    try:
        uptime_horas = round((time.time() - psutil.boot_time()) / 3600, 2)
    except Exception:
        uptime_horas = None

    try:
        users = psutil.users()
        ultimo_usuario = users[-1].name if users else _usuario_console()
    except Exception:
        ultimo_usuario = _usuario_console()

    try:
        bat = psutil.sensors_battery()
        bateria_percent = round(bat.percent, 1) if bat else None
        bateria_plugada = bool(bat.power_plugged) if bat else None
    except Exception:
        bateria_percent = bateria_plugada = None

    if bateria_percent is None:
        try:
            pmset = _out(["pmset", "-g", "batt"])
            m = re.search(r"(\d+)%", pmset)
            if m:
                bateria_percent = float(m.group(1))
            bateria_plugada = "AC Power" in pmset or "charging" in pmset.lower()
        except Exception:
            pass

    cpu_temp = _cpu_temp()

    ip_local = None
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET:
                ip = addr.address
                if ip.startswith("192.168.") and ip != "127.0.0.1":
                    ip_local = ip

    try:
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                procs.append({
                    "nome": p.info["name"],
                    "cpu":  round(p.info["cpu_percent"]    or 0, 1),
                    "mem":  round(p.info["memory_percent"] or 0, 1),
                })
            except Exception:
                pass
        procs.sort(key=lambda x: x["cpu"], reverse=True)
        top_processos = procs[:8]
    except Exception:
        top_processos = []

    return {
        "tipo_os":             "macos",   # sem isso o backend auto-cadastra como 'windows'
        "cpu_percent":         cpu_percent,
        "cpu_cores_percent":   json.dumps(cores_percent),
        "cpu_freq_mhz":        cpu_freq_mhz,
        "cpu_freq_max_mhz":    cpu_freq_max_mhz,
        "cpu_temp":            cpu_temp,
        "ram_percent":         round(ram.percent, 1),
        "ram_total_gb":        round(ram.total / (1024**3), 1),
        "disco_percent":       disco_percent,
        "disco_total_gb":      disco_total_gb,
        "disco_io_read_mbs":   disco_io_read_mbs,
        "disco_io_write_mbs":  disco_io_write_mbs,
        "net_recv_mbs":        net_recv_mbs,
        "net_sent_mbs":        net_sent_mbs,
        "uptime_horas":        uptime_horas,
        "ultimo_usuario":      ultimo_usuario,
        "bateria_percent":     bateria_percent,
        "bateria_plugada":     bateria_plugada,
        "top_processos":       json.dumps(top_processos, ensure_ascii=False),
        "hostname":            socket.gethostname(),
        "ip_local":            ip_local,
        "agente_hash":         _AGENTE_HASH,
    }

# ─── INVENTÁRIO (a cada 1h) ─────────────────────────────────────────────────
def coletar_inventario() -> dict:
    inv: dict = {"hostname": socket.gethostname()}
    log.info("Coletando inventário completo...")

    # CPU
    try:
        cpu_brand = _out(["sysctl", "-n", "machdep.cpu.brand_string"])
        if cpu_brand:
            inv["cpu_modelo"] = cpu_brand
    except Exception:
        pass

    try:
        cores_fis = _out_int(["sysctl", "-n", "hw.physicalcpu"])
        cores_log = _out_int(["sysctl", "-n", "hw.logicalcpu"])
        if cores_fis:
            inv["cpu_nucleos_fisicos"] = cores_fis
        if cores_log:
            inv["cpu_nucleos_logicos"] = cores_log
    except Exception:
        pass

    # RAM
    try:
        ram_bytes = _out_int(["sysctl", "-n", "hw.memsize"])
        if ram_bytes:
            inv["ram_total_gb"] = round(ram_bytes / (1024**3), 1)
    except Exception:
        pass

    try:
        sp_mem = _out(["system_profiler", "SPMemoryDataType", "-json"], timeout=30)
        if sp_mem:
            data = json.loads(sp_mem)
            slots = data.get("SPMemoryDataType", [{}])[0].get("_items", [])
            if slots:
                inv["ram_slots_usados"] = len(slots)
                s0 = slots[0]
                tipo = s0.get("dimm_type", "")
                if tipo:
                    inv["ram_tipo"] = tipo.split()[0]
                vel = re.search(r"(\d+)", s0.get("dimm_speed", ""))
                if vel:
                    inv["ram_velocidade_mhz"] = int(vel.group(1))
    except Exception:
        pass

    # GPU
    try:
        sp_gpu = _out(["system_profiler", "SPDisplaysDataType", "-json"], timeout=15)
        if sp_gpu:
            gpus = json.loads(sp_gpu).get("SPDisplaysDataType", [])
            if gpus:
                inv["gpu_modelo"] = gpus[0].get("sppci_model", "").strip() or None
    except Exception:
        pass

    # Disco
    try:
        sp_disk = _out(["system_profiler", "SPStorageDataType", "-json"], timeout=15)
        if sp_disk:
            disks = json.loads(sp_disk).get("SPStorageDataType", [])
            if disks:
                d = disks[0]
                inv["disco_modelo"] = d.get("_name", "").strip() or None
                medium = (d.get("spdisk_protocol", "") or d.get("spphysical_interconnect", "")).lower()
                inv["disco_tipo"] = ("NVMe" if "nvme" in medium else
                                     "SSD"  if ("ssd" in medium or "solid" in medium or "flash" in medium) else
                                     "HDD")
    except Exception:
        pass

    # Hardware info
    try:
        sp_hw = _out(["system_profiler", "SPHardwareDataType", "-json"], timeout=15)
        if sp_hw:
            hw = json.loads(sp_hw).get("SPHardwareDataType", [{}])[0]
            inv["maquina_fabricante"] = "Apple"
            inv["maquina_modelo"]     = hw.get("machine_model", "").strip() or None
            inv["maquina_serial"]     = hw.get("serial_number", "").strip() or None
    except Exception:
        pass

    # SO
    try:
        sw = _out(["sw_vers"])
        nome   = re.search(r"ProductName:\s*(.+)", sw)
        versao = re.search(r"ProductVersion:\s*(.+)", sw)
        build  = re.search(r"BuildVersion:\s*(.+)", sw)
        if nome and versao:
            inv["windows_versao"] = f"{nome.group(1).strip()} {versao.group(1).strip()}"
        if build:
            inv["windows_build"] = build.group(1).strip()
    except Exception:
        pass

    # Segurança
    try:
        fv = _out(["fdesetup", "status"])
        inv["bitlocker_ativo"] = "On" in fv or "enabled" in fv.lower()
    except Exception:
        pass

    try:
        fw = _out(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"])
        inv["firewall_ativo"] = "enabled" in fw.lower()
    except Exception:
        pass

    try:
        sip = _out(["csrutil", "status"])
        inv["sip_ativo"] = "enabled" in sip.lower()
    except Exception:
        pass

    # Wi-Fi
    try:
        airport = ("/System/Library/PrivateFrameworks/Apple80211.framework"
                   "/Versions/Current/Resources/airport")
        ssid_out = _out([airport, "-I"])
        m = re.search(r"\bSSID:\s*(.+)", ssid_out)
        if m:
            inv["wifi_ssid"] = m.group(1).strip()
        rssi = re.search(r"\bagrCtlRSSI:\s*(-?\d+)", ssid_out)
        if rssi:
            inv["wifi_sinal_dbm"] = int(rssi.group(1))
    except Exception:
        pass

    # MACs / gateway / DNS
    try:
        macs = []
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if hasattr(addr, "family") and addr.family.name == "AF_LINK" and addr.address:
                    mac = addr.address
                    if mac not in ("00:00:00:00:00:00", "") and mac not in macs:
                        macs.append(mac)
        if macs:
            inv["mac_addresses"] = ", ".join(macs)
    except Exception:
        pass

    try:
        gw = _out_s("route -n get default 2>/dev/null | awk '/gateway/{print $2}'")
        if gw:
            inv["gateway"] = gw
    except Exception:
        pass

    try:
        dns = _out_s("scutil --dns 2>/dev/null | awk '/nameserver/{print $3}' | sort -u | head -3 | tr '\\n' ','")
        if dns:
            inv["dns_servidores"] = dns.rstrip(",")
    except Exception:
        pass

    # Software
    for app_name, inv_key, plist_path in [
        ("Google Chrome",   "chrome_versao", "/Applications/Google Chrome.app/Contents/Info.plist"),
        ("Microsoft Word",  "office_versao",  "/Applications/Microsoft Word.app/Contents/Info.plist"),
        ("Microsoft Teams", "teams_versao",   "/Applications/Microsoft Teams.app/Contents/Info.plist"),
    ]:
        try:
            v = _out(["defaults", "read", plist_path, "CFBundleShortVersionString"])
            if v:
                inv[inv_key] = v
        except Exception:
            pass

    # Falhas de login (últimas 24h via log)
    try:
        raw = _out_s(
            "log show --last 24h --predicate "
            "'eventMessage CONTAINS \"Authentication failed\"' "
            "--style syslog 2>/dev/null | grep -c 'Authentication'",
            timeout=15
        )
        if raw:
            inv["falhas_login_24h"] = max(0, int(raw.strip()))
    except Exception:
        pass

    log.info(f"Inventário coletado — {len(inv)} campos")
    return inv

# ─── EVENTOS DO SISTEMA ─────────────────────────────────────────────────────
def coletar_eventos_log() -> list:
    try:
        raw = _out_s(
            "log show --last 7d --predicate "
            "'(eventMessage CONTAINS \"panic\" OR eventMessage CONTAINS \"shutdown\" "
            "OR eventMessage CONTAINS \"reboot\") AND "
            "senderImagePath CONTAINS \"kernel\"' "
            "--style json 2>/dev/null | head -c 65536",
            timeout=30
        )
        if not raw:
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        eventos = []
        for e in data[:50]:
            eventos.append({
                "event_id":  0,
                "source":    e.get("senderImagePath", "kernel"),
                "level":     e.get("messageType", "Default"),
                "message":   str(e.get("eventMessage", ""))[:300],
                "timestamp": e.get("timestamp", ""),
            })
        return eventos
    except Exception:
        return []

def enviar_eventos_log(config: dict, eventos: list) -> bool:
    if not eventos:
        return True
    pat = config.get("patrimonio", "")
    for base_url in _api_urls(config):
        url = f"{base_url.rstrip('/')}/api/noc/maquinas/{pat}/eventos"
        try:
            r = requests.post(url, json=eventos, headers=_headers(config), timeout=15)
            if r.status_code == 200:
                log.info(f"OK Eventos enviados: {len(eventos)} registros")
                return True
        except Exception as e:
            log.debug(f"Erro eventos {base_url}: {e}")
    return False

# ─── Envio ──────────────────────────────────────────────────────────────────
def enviar_metricas(config: dict, metricas: dict) -> bool:
    pat = config.get("patrimonio", "")
    for base_url in _api_urls(config):
        url = f"{base_url.rstrip('/')}/api/noc/maquinas/{pat}/metricas"
        try:
            r = requests.post(url, json=metricas, headers=_headers(config), timeout=8)
            if r.status_code == 200:
                temp_s = f"  Temp={metricas['cpu_temp']}C" if metricas.get("cpu_temp") else ""
                bat_s  = f"  Bat={metricas['bateria_percent']}%" if metricas.get("bateria_percent") else ""
                log.info(f"OK Metricas  CPU={metricas['cpu_percent']}%  RAM={metricas['ram_percent']}%"
                         f"  HD={metricas.get('disco_percent','?')}%{temp_s}{bat_s}")
                return True
            log.warning(f"Metricas HTTP {r.status_code}: {r.text[:100]}")
        except requests.exceptions.ConnectionError:
            log.debug(f"Sem conexão com {base_url}")
        except Exception as e:
            log.warning(f"Erro métricas {base_url}: {e}")
    log.warning("Métricas: nenhum servidor respondeu")
    return False

def enviar_inventario(config: dict, inventario: dict) -> bool:
    pat = config.get("patrimonio", "")
    for base_url in _api_urls(config):
        url = f"{base_url.rstrip('/')}/api/noc/maquinas/{pat}/inventario"
        try:
            r = requests.post(url, json=inventario, headers=_headers(config), timeout=30)
            if r.status_code == 200:
                log.info(f"OK Inventário enviado  [{base_url}]")
                return True
            log.warning(f"Inventário HTTP {r.status_code}: {r.text[:100]}")
        except Exception as e:
            log.warning(f"Erro inventário {base_url}: {e}")
    log.warning("Inventário: nenhum servidor respondeu")
    return False

# ─── BLOQUEAR / DESBLOQUEAR ─────────────────────────────────────────────────
def bloquear_maquina() -> dict:
    usuario = _usuario_console()
    if not usuario:
        return {"status": "erro", "msg": "Nenhum usuário logado no console"}

    if usuario.lower() in USUARIOS_PROTEGIDOS:
        return {"status": "erro", "msg": f"Usuário protegido: {usuario}"}

    log.info(f"[CMD] BLOQUEAR — usuário: {usuario}")

    try:
        BLOQUEADO_TXT.write_text(usuario, encoding="utf-8")
    except Exception as e:
        return {"status": "erro", "msg": f"Erro ao salvar usuário: {e}"}

    erros = []

    try:
        r = _sh(["dscl", ".", "-passwd", f"/Users/{usuario}", "ComplexA#Bloqueado!"])
        if r.returncode != 0:
            erros.append(f"dscl passwd: {r.stderr.strip()}")
    except Exception as e:
        erros.append(f"dscl passwd: {e}")

    try:
        r = _sh(["dscl", ".", "-create", f"/Users/{usuario}", "UserShell", "/usr/bin/false"])
        if r.returncode != 0:
            erros.append(f"dscl shell: {r.stderr.strip()}")
    except Exception as e:
        erros.append(f"dscl shell: {e}")

    try:
        _sh(["defaults", "write", "/Library/Preferences/com.apple.loginwindow",
             "LoginwindowText",
             "Computador bloqueado pela TI. Entre em contato: ti@bluepaysolutions.com.br"])
    except Exception as e:
        erros.append(f"loginwindow text: {e}")

    time.sleep(2)

    try:
        _sh(["pkill", "-9", "-u", usuario, "loginwindow"])
    except Exception as e:
        erros.append(f"pkill: {e}")

    if erros:
        log.warning(f"Bloqueio parcial — {erros}")
        return {"status": "parcial", "msg": "; ".join(erros), "usuario": usuario}

    log.info(f"[CMD] BLOQUEAR OK — {usuario}")
    return {"status": "ok", "usuario": usuario}

def desbloquear_maquina() -> dict:
    if not BLOQUEADO_TXT.exists():
        return {"status": "erro", "msg": "Nenhum usuário bloqueado registrado"}

    usuario = BLOQUEADO_TXT.read_text(encoding="utf-8").strip()
    if not usuario:
        return {"status": "erro", "msg": "Arquivo usuario_bloqueado.txt vazio"}

    log.info(f"[CMD] DESBLOQUEAR — usuário: {usuario}")
    erros = []

    try:
        r = _sh(["dscl", ".", "-passwd", f"/Users/{usuario}", "Suporte@123"])
        if r.returncode != 0:
            erros.append(f"dscl passwd: {r.stderr.strip()}")
    except Exception as e:
        erros.append(f"dscl passwd: {e}")

    try:
        r = _sh(["dscl", ".", "-create", f"/Users/{usuario}", "UserShell", "/bin/zsh"])
        if r.returncode != 0:
            erros.append(f"dscl shell: {r.stderr.strip()}")
    except Exception as e:
        erros.append(f"dscl shell: {e}")

    try:
        _sh(["defaults", "delete", "/Library/Preferences/com.apple.loginwindow", "LoginwindowText"])
    except Exception:
        pass

    try:
        BLOQUEADO_TXT.unlink(missing_ok=True)
    except Exception as e:
        erros.append(f"rm bloqueado: {e}")

    if erros:
        log.warning(f"Desbloqueio parcial — {erros}")
        return {"status": "parcial", "msg": "; ".join(erros), "usuario": usuario}

    log.info(f"[CMD] DESBLOQUEAR OK — {usuario}")
    return {"status": "ok", "usuario": usuario}

# ─── COMANDOS REMOTOS ───────────────────────────────────────────────────────
_COMANDOS_RESPONDIDOS: set = set()

def processar_comandos(config: dict):
    pat = config.get("patrimonio", "")
    comandos = []
    for base_url in _api_urls(config):
        url = f"{base_url.rstrip('/')}/api/noc/maquinas/{pat}/comandos"
        try:
            r = requests.get(url, headers=_headers(config), timeout=10)
            if r.status_code != 200:
                log.debug(f"Comandos HTTP {r.status_code}")
                return
            data = r.json()
            if isinstance(data, list):
                comandos = data
            elif isinstance(data.get("comando"), dict):
                comandos = [data["comando"]]
            elif isinstance(data.get("comandos"), list):
                comandos = data["comandos"]
            else:
                comandos = []
            log.debug(f"Verificando comandos para patrimônio={pat}: {len(comandos)} pendente(s)")
            break
        except requests.exceptions.ConnectionError:
            return
        except Exception as e:
            log.debug(f"Erro comandos {base_url}: {e}")
            return

    for cmd in comandos:
        cmd_id = cmd.get("id")
        acao   = (cmd.get("acao") or cmd.get("tipo") or "").lower()
        if cmd_id in _COMANDOS_RESPONDIDOS:
            continue

        log.info(f"[CMD] Recebido: id={cmd_id} acao={acao}")
        resultado = None

        if acao in ("bloquear", "block"):
            resultado = bloquear_maquina()

        elif acao in ("desbloquear", "unblock"):
            resultado = desbloquear_maquina()

        elif acao in ("reiniciar", "reboot"):
            log.info("[CMD] REINICIAR")
            resultado = {"status": "ok"}
            _responder_comando(config, pat, cmd_id, resultado)
            _COMANDOS_RESPONDIDOS.add(cmd_id)
            time.sleep(3)
            _sh(["shutdown", "-r", "now"])
            return

        elif acao in ("desligar", "shutdown"):
            log.info("[CMD] DESLIGAR")
            resultado = {"status": "ok"}
            _responder_comando(config, pat, cmd_id, resultado)
            _COMANDOS_RESPONDIDOS.add(cmd_id)
            time.sleep(3)
            _sh(["shutdown", "-h", "now"])
            return

        elif acao in ("info", "ping"):
            resultado = {
                "status":          "ok",
                "hostname":        socket.gethostname(),
                "ip_local":        _get_ip_local(),
                "usuario_console": _usuario_console(),
            }

        else:
            log.warning(f"[CMD] Ação desconhecida: {acao}")
            resultado = {"status": "erro", "msg": f"Ação desconhecida: {acao}"}

        if resultado is not None:
            _responder_comando(config, pat, cmd_id, resultado)
            _COMANDOS_RESPONDIDOS.add(cmd_id)

def _responder_comando(config: dict, pat: str, cmd_id, resultado: dict):
    for base_url in _api_urls(config):
        url = f"{base_url.rstrip('/')}/api/noc/maquinas/{pat}/comandos/{cmd_id}/resposta"
        try:
            r = requests.post(url, json=resultado, headers=_headers(config), timeout=10)
            if r.status_code in (200, 201):
                log.info(f"[CMD] Resposta enviada: id={cmd_id} status={resultado.get('status')}")
                return
            log.warning(f"[CMD] Resposta HTTP {r.status_code}: {r.text[:100]}")
        except Exception as e:
            log.warning(f"[CMD] Erro resposta {base_url}: {e}")

def _get_ip_local() -> str | None:
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and addr.address.startswith("192.168."):
                return addr.address
    return None

# ─── Auto-atualização ────────────────────────────────────────────────────────
def verificar_atualizacao(config: dict) -> bool:
    hash_local = _hash_arquivo(AGENTE_PATH)
    if not hash_local:
        return False

    for base_url in _api_urls(config):
        try:
            r = requests.get(
                f"{base_url.rstrip('/')}/api/install/mac/version",
                headers=_headers(config), timeout=8
            )
            if r.status_code != 200:
                continue
            info = r.json()
            hash_srv = info.get("sha256", "")
            if not hash_srv or hash_srv == hash_local:
                log.debug("Auto-update: sem atualização disponível")
                return False

            log.info(f"Auto-update: nova versão ({hash_srv[:12]}…). Baixando…")

            r2 = requests.get(
                f"{base_url.rstrip('/')}/api/install/mac",
                headers=_headers(config), timeout=120, stream=True
            )
            if r2.status_code != 200:
                log.warning(f"Auto-update: falha ao baixar ({r2.status_code})")
                continue

            novo = Path(str(AGENTE_PATH) + ".new")
            with open(novo, "wb") as f:
                for chunk in r2.iter_content(65536):
                    f.write(chunk)

            if _hash_arquivo(novo) != hash_srv:
                log.warning("Auto-update: hash não confere. Abortando.")
                novo.unlink(missing_ok=True)
                continue

            novo.replace(AGENTE_PATH)
            log.info("Auto-update: substituído. Reiniciando via launchctl…")
            subprocess.Popen(
                ["launchctl", "kickstart", "-k", "system/com.bluecontrol.metricas"],
                close_fds=True
            )
            time.sleep(3)
            sys.exit(0)

        except Exception as e:
            log.warning(f"Auto-update: erro ao verificar {base_url}: {e}")

    return False

# ─── Loop principal ─────────────────────────────────────────────────────────
_RUNNING = True

def _handle_signal(sig, frame):
    global _RUNNING
    log.info(f"Sinal {sig} recebido — encerrando...")
    _RUNNING = False

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)

def main():
    global _AGENTE_HASH, _RUNNING

    log.info("==========================================")
    log.info("  BlueControl IT - Agente Mac v1")
    log.info(f"  Máquina: {socket.gethostname()}")
    log.info("==========================================")

    _AGENTE_HASH = _hash_arquivo(AGENTE_PATH)
    if _AGENTE_HASH:
        log.info(f"  Hash do agente: {_AGENTE_HASH[:16]}...")

    _ultimo_metricas     = 0.0
    _ultimo_inventario   = 0.0
    _ultimo_update_check = 0.0
    _ultimo_comandos     = 0.0

    while _RUNNING:
        agora = time.time()
        try:
            config = carregar_config()
        except FileNotFoundError as e:
            log.error(str(e))
            time.sleep(30)
            continue
        except Exception as e:
            log.error(f"Erro ao carregar config: {e}")
            time.sleep(30)
            continue

        try:
            if agora - _ultimo_comandos >= 60:
                processar_comandos(config)
                _ultimo_comandos = time.time()

            if agora - _ultimo_metricas >= 300:
                metricas = coletar_metricas()
                enviar_metricas(config, metricas)
                _ultimo_metricas = time.time()

            if agora - _ultimo_inventario >= 3600:
                inventario = coletar_inventario()
                if enviar_inventario(config, inventario):
                    _ultimo_inventario = time.time()
                eventos = coletar_eventos_log()
                enviar_eventos_log(config, eventos)

            if agora - _ultimo_update_check >= 3600:
                _ultimo_update_check = time.time()
                verificar_atualizacao(config)

        except Exception as e:
            log.error(f"Erro inesperado no loop: {e}")

        for _ in range(12):
            if not _RUNNING:
                break
            time.sleep(5)

    log.info("Agente encerrado.")

if __name__ == "__main__":
    main()
