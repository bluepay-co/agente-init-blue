"""
BlueControl IT — Agente de Métricas v3
========================================
Coleta métricas (a cada 5 min) e inventário completo (a cada 1h).

Instalar dependências:
  pip install pyinstaller psutil requests wmi pywin32

Compilar:
  pyinstaller --onefile --noconsole
    --hidden-import=wmi --hidden-import=pythoncom --hidden-import=pywintypes
    --name "BlueControl_Metricas" agente_metricas.py

Config: C:\\BlueControl\\config.json   Log: C:\\BlueControl\\metricas.log
"""

import os, sys, json, time, socket, logging, re, subprocess, winreg
import requests, psutil
import io as _io
from pathlib import Path
import shutil

try:
    import servicemanager, win32service, win32serviceutil, win32event
    _SVC_OK = True
except ImportError:
    _SVC_OK = False

# ─── Caminhos ──────────────────────────────────────────────────────────────
CONFIG_PATH = Path(r"C:\BlueControl\config.json")
LOG_PATH    = Path(r"C:\BlueControl\metricas.log")

# ─── Logging ───────────────────────────────────────────────────────────────
os.makedirs(r"C:\BlueControl", exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH), level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("bluecontrol_metricas")
try:
    _con = logging.StreamHandler(
        _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    )
except AttributeError:
    _con = logging.StreamHandler(sys.stdout)
_con.setLevel(logging.INFO)
_con.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
log.addHandler(_con)

# ─── WMI ───────────────────────────────────────────────────────────────────
try:
    import wmi as _wmi_mod
    _WMI_OK = True
except ImportError:
    _WMI_OK = False

# ─── Helpers ───────────────────────────────────────────────────────────────
def _ps(cmd: str, timeout: int = 20) -> str:
    """Executa PowerShell e retorna stdout limpo."""
    try:
        r = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip()
    except Exception:
        return ""

def _ps_int(cmd: str, timeout: int = 20) -> int | None:
    v = _ps(cmd, timeout)
    try:
        return int(v)
    except Exception:
        return None

def _ps_bool(cmd: str) -> bool | None:
    v = _ps(cmd).lower()
    if v in ("true", "1", "on"):  return True
    if v in ("false", "0", "off"): return False
    return None

def _reg_versao(*chaves: str) -> str | None:
    """Busca versão de software no Registro do Windows."""
    paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    busca = [c.lower() for c in chaves]
    for hive, path in paths:
        try:
            key = winreg.OpenKey(hive, path)
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                    nome = winreg.QueryValueEx(sub, "DisplayName")[0]
                    if any(b in nome.lower() for b in busca):
                        return winreg.QueryValueEx(sub, "DisplayVersion")[0]
                except Exception:
                    pass
        except Exception:
            pass
    return None

# ─── Config ────────────────────────────────────────────────────────────────
def carregar_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config nao encontrado: {CONFIG_PATH}")
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)

# ─── Temperatura CPU ────────────────────────────────────────────────────────
def _cpu_temp() -> float | None:
    # Método 1: MSAcpi via root\wmi
    if _WMI_OK:
        try:
            w = _wmi_mod.WMI(namespace=r"root\wmi")
            zonas = w.MSAcpi_ThermalZoneTemperature()
            temps = []
            for z in zonas:
                t = round(z.CurrentTemperature / 10.0 - 273.15, 1)
                if 0 < t < 120:
                    temps.append(t)
            if temps:
                return max(temps)
        except Exception:
            pass

    # Método 2: PowerShell como fallback
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature "
             "-ErrorAction SilentlyContinue | Select-Object -First 1).CurrentTemperature"],
            timeout=10, stderr=subprocess.DEVNULL
        ).decode().strip()
        val = float(out)
        if val > 0:
            return round(val / 10.0 - 273.15, 1)
    except Exception:
        pass

    return None

# ─── Hash do próprio EXE (calculado uma vez na inicialização) ───────────────
def _hash_self() -> str:
    try:
        import hashlib
        exe = Path(r"C:\BlueControl\BlueControl_Metricas.exe")
        if not exe.exists():
            log.warning(f"_hash_self: EXE nao encontrado em {exe}")
            return ""
        h = hashlib.sha256()
        with open(exe, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        log.warning(f"_hash_self erro: {e}")
        return ""

_AGENTE_HASH: str = ""  # calculado uma vez no início do loop

# ─── MÉTRICAS (a cada 5 min) ────────────────────────────────────────────────
def coletar_metricas() -> dict:
    # Inicializa contadores (1ª chamada descartada)
    psutil.cpu_percent(interval=None)
    psutil.cpu_percent(percpu=True, interval=None)
    for _p in psutil.process_iter(["pid", "cpu_percent"]):
        pass
    net_t0 = psutil.net_io_counters()
    try:   disk_t0 = psutil.disk_io_counters()
    except Exception: disk_t0 = None

    time.sleep(1)  # janela de 1 segundo

    cpu_percent = round(psutil.cpu_percent(interval=None), 1)
    try:   cores_percent = [round(c, 1) for c in psutil.cpu_percent(percpu=True, interval=None)]
    except Exception: cores_percent = []

    try:
        freq = psutil.cpu_freq()
        cpu_freq_mhz     = round(freq.current, 0) if freq else None
        cpu_freq_max_mhz = round(freq.max,     0) if (freq and freq.max) else None
    except Exception:
        cpu_freq_mhz = cpu_freq_max_mhz = None

    ram = psutil.virtual_memory()

    try:
        disco          = psutil.disk_usage("C:\\")
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
        # GetTickCount64 é mais confiável no Windows pois reseta mesmo com Fast Startup
        # (psutil.boot_time pode não resetar com Fast Startup ativado)
        import ctypes
        uptime_ms = ctypes.windll.kernel32.GetTickCount64()
        uptime_horas = round(uptime_ms / 1000 / 3600, 2)
    except Exception:
        try:   uptime_horas = round((time.time() - psutil.boot_time()) / 3600, 2)
        except Exception: uptime_horas = None

    try:
        users = psutil.users()
        ultimo_usuario = users[-1].name if users else None
    except Exception:
        try:    ultimo_usuario = os.getlogin()
        except Exception: ultimo_usuario = None

    try:
        bat = psutil.sensors_battery()
        bateria_percent = round(bat.percent, 1) if bat else None
        bateria_plugada = bool(bat.power_plugged) if bat else None
    except Exception:
        bateria_percent = bateria_plugada = None

    cpu_temp = _cpu_temp()

    # Detect IPs
    ip_local = None
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET:
                ip = addr.address
                if ip.startswith('192.168.') and ip != '127.0.0.1':
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
    """Coleta hardware, SO, software, rede e segurança — pode levar até 30s."""
    inv: dict = {"hostname": socket.gethostname()}
    log.info("Coletando inventario completo...")

    # ── Hardware via WMI ──────────────────────────────────────────────────
    if _WMI_OK:
        try:
            w = _wmi_mod.WMI()
            # CPU
            cpu = w.Win32_Processor()[0]
            inv["cpu_modelo"]          = (cpu.Name or "").strip()
            inv["cpu_nucleos_fisicos"] = int(cpu.NumberOfCores or 0) or None
            inv["cpu_nucleos_logicos"] = int(cpu.NumberOfLogicalProcessors or 0) or None
        except Exception: pass

        try:
            w = _wmi_mod.WMI()
            gpus = [g for g in w.Win32_VideoController()
                    if g.Name and "microsoft" not in g.Name.lower() and "basic" not in g.Name.lower()]
            if gpus:
                inv["gpu_modelo"] = gpus[0].Name.strip()
        except Exception: pass

        try:
            w = _wmi_mod.WMI()
            mems = list(w.Win32_PhysicalMemory())
            inv["ram_slots_usados"] = len(mems)
            try:
                arr = list(w.Win32_PhysicalMemoryArray())
                inv["ram_slots_total"] = int(arr[0].MemoryDevices) if arr else len(mems)
            except Exception:
                inv["ram_slots_total"] = len(mems)
            if mems:
                tipo_map = {20:"DDR", 21:"DDR2", 24:"DDR3", 26:"DDR4", 34:"DDR5"}
                inv["ram_tipo"]          = tipo_map.get(int(mems[0].MemoryType or 0), "DDR")
                inv["ram_velocidade_mhz"] = int(mems[0].Speed or 0) or None
        except Exception: pass

        try:
            w = _wmi_mod.WMI()
            disks = list(w.Win32_DiskDrive())
            if disks:
                d   = disks[0]
                mod = (d.Model or "").strip()
                inv["disco_modelo"] = mod
                lo  = mod.lower()
                inv["disco_tipo"] = ("NVMe" if "nvme" in lo
                                     else "SSD" if ("ssd" in lo or "solid" in lo or "m.2" in lo)
                                     else "HDD")
        except Exception: pass

        try:
            w  = _wmi_mod.WMI()
            cs = w.Win32_ComputerSystem()[0]
            inv["maquina_fabricante"] = (cs.Manufacturer or "").strip()
            inv["maquina_modelo"]     = (cs.Model or "").strip()
            bi = w.Win32_BIOS()[0]
            inv["maquina_serial"]     = (bi.SerialNumber or "").strip()
        except Exception: pass

        try:
            w = _wmi_mod.WMI()
            os_info = w.Win32_OperatingSystem()[0]
            inv["windows_versao"] = (os_info.Caption or "").strip()
            inv["windows_build"]  = str(os_info.BuildNumber or "")
        except Exception: pass

    # ── Windows Activation ────────────────────────────────────────────────
    try:
        v = _ps(
            'try{$c=(Get-CimInstance SoftwareLicensingProduct | '
            'Where-Object{$_.Name -like "*Windows*" -and $_.LicenseStatus -eq 1} | '
            'Measure-Object).Count; $c -gt 0}catch{"False"}'
        )
        if v: inv["windows_ativado"] = v == "True"
    except Exception: pass

    # ── Pending Updates (COM — até 30s) ──────────────────────────────────
    try:
        n = _ps_int(
            'try{$s=New-Object -ComObject Microsoft.Update.Session; '
            '$r=$s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0"); '
            '$r.Updates.Count}catch{-1}',
            timeout=35
        )
        if n is not None and n >= 0:
            inv["atualizacoes_pend"] = n
    except Exception: pass

    # ── Reboot Pending ────────────────────────────────────────────────────
    try:
        inv["reboot_pendente"] = _ps_bool(
            '$r=@("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired",'
            '"HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending",'
            '"HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\PendingFileRenameOperations");'
            '($r | Where-Object{Test-Path $_}).Count -gt 0'
        ) or False
    except Exception: pass

    # ── Último update instalado ───────────────────────────────────────────
    try:
        d = _ps('(Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1).InstalledOn.ToString("yyyy-MM-dd")')
        if d and len(d) == 10: inv["ultimo_update"] = d
    except Exception: pass

    # ── Software via Registro ─────────────────────────────────────────────
    inv["chrome_versao"] = _reg_versao("Google Chrome")
    inv["office_versao"] = _reg_versao("Microsoft 365", "Microsoft Office")
    inv["teams_versao"]  = _reg_versao("Microsoft Teams")

    # ── Wi-Fi ────────────────────────────────────────────────────────────
    try:
        wifi_out = _ps("netsh wlan show interfaces")
        ssid_m = re.search(r"SSID\s*:\s*(.+)", wifi_out)
        if ssid_m:
            inv["wifi_ssid"] = ssid_m.group(1).strip()
        # Tenta obter sinal em dBm via WMI
        if _WMI_OK:
            try:
                w2 = _wmi_mod.WMI(namespace=r"root\wmi")
                for item in w2.MSNdis_80211_ReceivedSignalStrength():
                    dbm = getattr(item, "Ndis80211ReceivedSignalStrength", None)
                    if dbm is not None:
                        inv["wifi_sinal_dbm"] = int(dbm)
                        break
            except Exception:
                pass
    except Exception: pass

    # ── MACs e Gateway / DNS ──────────────────────────────────────────────
    try:
        macs = []
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if hasattr(addr, "family") and addr.family.name == "AF_LINK" and addr.address:
                    mac = addr.address
                    if mac not in ("00-00-00-00-00-00", "00:00:00:00:00:00") and mac not in macs:
                        macs.append(mac)
        if macs:
            inv["mac_addresses"] = ", ".join(macs)
    except Exception: pass

    try:
        gw = _ps('(Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue | '
                 'Sort-Object RouteMetric | Select-Object -First 1).NextHop')
        if gw: inv["gateway"] = gw
    except Exception: pass

    try:
        dns = _ps('(Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | '
                  'Where-Object{$_.ServerAddresses} | Select-Object -First 1).ServerAddresses -join ", "')
        if dns: inv["dns_servidores"] = dns
    except Exception: pass

    # ── Segurança — BitLocker ─────────────────────────────────────────────
    try:
        bl = _ps('(Get-BitLockerVolume -MountPoint "C:" -EA SilentlyContinue).ProtectionStatus')
        if bl: inv["bitlocker_ativo"] = (bl.strip() == "On")
    except Exception: pass

    # ── Segurança — Windows Defender ──────────────────────────────────────
    try:
        def_out = _ps(
            '$s=Get-MpComputerStatus -EA SilentlyContinue; '
            'if($s){"$($s.RealTimeProtectionEnabled)|$($s.AntivirusSignatureLastUpdated.ToString(\'yyyy-MM-dd\'))"}else{""}'
        )
        if "|" in def_out:
            p = def_out.split("|")
            inv["defender_ativo"]       = p[0].strip().lower() == "true"
            inv["defender_assinatura"] = p[1] if (len(p) > 1 and p[1] != "0001-01-01") else None
    except Exception: pass

    # ── Segurança — Firewall ──────────────────────────────────────────────
    try:
        fw = _ps(
            '(Get-NetFirewallProfile -Profile Domain,Public,Private -EA SilentlyContinue | '
            'Where-Object{$_.Enabled -eq $true} | Measure-Object).Count -ge 1'
        )
        if fw: inv["firewall_ativo"] = (fw == "True")
    except Exception: pass

    # ── Segurança — Eventos (Event Log) ──────────────────────────────────
    try:
        inv["falhas_login_24h"] = _ps_int(
            'try{(Get-WinEvent -FilterHashtable @{LogName="Security";Id=4625;'
            'StartTime=(Get-Date).AddHours(-24)} -EA SilentlyContinue | Measure-Object).Count}catch{0}'
        ) or 0
    except Exception: pass

    try:
        inv["bsods_30d"] = _ps_int(
            'try{(Get-WinEvent -FilterHashtable @{LogName="System";Id=1001;'
            'ProviderName="Microsoft-Windows-WER-SystemErrorReporting";'
            'StartTime=(Get-Date).AddDays(-30)} -EA SilentlyContinue | Measure-Object).Count}catch{0}'
        ) or 0
    except Exception: pass

    try:
        inv["erros_criticos_24h"] = _ps_int(
            'try{(Get-WinEvent -FilterHashtable @{LogName="System";Level=2;'
            'StartTime=(Get-Date).AddHours(-24)} -EA SilentlyContinue | Measure-Object).Count}catch{0}'
        ) or 0
    except Exception: pass

    log.info(f"Inventario coletado — {len(inv)} campos")
    return inv

# ─── EVENTOS DO WINDOWS EVENT LOG ───────────────────────────────────────────
_EVENTOS_PS = r"""
$start = (Get-Date).AddDays(-7)
$result = @()
$filtros = @(
    @{LogName='System'; Id=6008},
    @{LogName='System'; Id=41;   ProviderName='Microsoft-Windows-Kernel-Power'},
    @{LogName='System'; Id=1074},
    @{LogName='System'; Id=6005},
    @{LogName='System'; Id=6006},
    @{LogName='Application'; Id=1000}
)
foreach ($f in $filtros) {
    $f.StartTime = $start
    try {
        $evts = Get-WinEvent -FilterHashtable $f -MaxEvents 10 -EA SilentlyContinue
        foreach ($e in $evts) {
            $msg = ($e.Message -replace '\s+', ' ').Substring(0, [Math]::Min(300, $e.Message.Length))
            $result += [PSCustomObject]@{
                event_id  = $e.Id
                source    = $e.ProviderName
                level     = $e.LevelDisplayName
                message   = $msg
                timestamp = $e.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')
            }
        }
    } catch {}
}
$result | Sort-Object timestamp -Descending | Select-Object -First 50 | ConvertTo-Json -Compress
"""

def coletar_eventos_log() -> list:
    raw = _ps(_EVENTOS_PS, timeout=30)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        return data
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
            log.warning(f"Eventos HTTP {r.status_code}: {r.text[:100]}")
        except Exception as e:
            log.warning(f"Erro eventos {base_url}: {e}")
    return False

# ─── Envios ─────────────────────────────────────────────────────────────────
def _headers(config: dict) -> dict:
    # "token_agente" é o nome gravado pelo ConfiguradorAgente. Sem padrão no
    # código: um token conhecido na fonte vale tanto quanto nenhum.
    token = config.get("agent_secret") or config.get("token_agente")
    if not token:
        raise SystemExit("[config] 'agent_secret' ausente na config do agente.")
    return {
        "X-Agent-Token": token,
        "Content-Type": "application/json",
    }

def _api_urls(config: dict) -> list[str]:
    # "url_api" é o nome gravado pelo ConfiguradorAgente
    return (config.get("api_urls")
            or [config.get("api_url") or config.get("url_api") or "http://192.168.0.17:8550"])

def enviar_metricas(config: dict, metricas: dict) -> bool:
    pat = config.get("patrimonio", "")
    for base_url in _api_urls(config):
        url = f"{base_url.rstrip('/')}/api/noc/maquinas/{pat}/metricas"
        try:
            r = requests.post(url, json=metricas, headers=_headers(config), timeout=8)
            if r.status_code == 200:
                temp_s = f"  Temp={metricas['cpu_temp']}C"  if metricas.get("cpu_temp")       else ""
                bat_s  = f"  Bat={metricas['bateria_percent']}%" if metricas.get("bateria_percent") else ""
                log.info(f"OK Metricas  CPU={metricas['cpu_percent']}%  RAM={metricas['ram_percent']}%"
                         f"  HD={metricas.get('disco_percent','?')}%{temp_s}{bat_s}")
                return True
            log.warning(f"Metricas HTTP {r.status_code}: {r.text[:100]}")
        except requests.exceptions.ConnectionError:
            log.debug(f"Sem conexao com {base_url}")
        except Exception as e:
            log.warning(f"Erro metricas {base_url}: {e}")
    log.warning("Metricas: nenhum servidor respondeu")
    return False

def enviar_inventario(config: dict, inventario: dict) -> bool:
    pat = config.get("patrimonio", "")
    for base_url in _api_urls(config):
        url = f"{base_url.rstrip('/')}/api/noc/maquinas/{pat}/inventario"
        try:
            r = requests.post(url, json=inventario, headers=_headers(config), timeout=30)
            if r.status_code == 200:
                log.info(f"OK Inventario enviado  [{base_url}]")
                return True
            log.warning(f"Inventario HTTP {r.status_code}: {r.text[:100]}")
        except Exception as e:
            log.warning(f"Erro inventario {base_url}: {e}")
    log.warning("Inventario: nenhum servidor respondeu")
    return False

# ─── Constantes ─────────────────────────────────────────────────────────────
SVC_NAME    = "BlueControlMetricas"
SVC_DISPLAY = "BlueControl IT - Agente de Metricas"
SVC_DESC    = "Coleta metricas e inventario para o BlueControl IT."
INSTALL_DIR = Path(r"C:\BlueControl")
EXE_NAME    = "BlueControl_Metricas.exe"

# ─── Auto-atualização ────────────────────────────────────────────────────────
def _hash_exe(path: Path) -> str:
    """Retorna SHA-256 do arquivo (para comparar versões)."""
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""

def verificar_atualizacao(config: dict) -> bool:
    """
    Verifica se há uma versão nova do agente no servidor.
    Se houver: baixa, substitui o EXE e reinicia o serviço via script .bat.
    Retorna True se iniciou atualização (processo vai encerrar em seguida).
    """
    exe_atual = INSTALL_DIR / EXE_NAME
    hash_local = _hash_exe(exe_atual)
    if not hash_local:
        return False  # EXE não encontrado, não tenta atualizar

    for base_url in _api_urls(config):
        try:
            r = requests.get(
                f"{base_url.rstrip('/')}/api/install/windows/version",
                headers=_headers(config), timeout=8
            )
            if r.status_code != 200:
                continue
            info = r.json()
            hash_srv = info.get("sha256", "")
            if not hash_srv or hash_srv == hash_local:
                log.debug("Auto-update: sem atualização disponível.")
                return False

            log.info(f"Auto-update: nova versão disponível ({hash_srv[:12]}…). Baixando…")

            # Baixa novo EXE
            r2 = requests.get(
                f"{base_url.rstrip('/')}/api/install/windows",
                headers=_headers(config), timeout=120, stream=True
            )
            if r2.status_code != 200:
                log.warning(f"Auto-update: falha ao baixar EXE ({r2.status_code})")
                continue

            novo_exe = INSTALL_DIR / "BlueControl_Metricas_new.exe"
            with open(novo_exe, "wb") as f:
                for chunk in r2.iter_content(65536):
                    f.write(chunk)

            # Verifica hash do arquivo baixado
            hash_baixado = _hash_exe(novo_exe)
            if hash_baixado != hash_srv:
                log.warning("Auto-update: hash do arquivo baixado não confere. Abortando.")
                novo_exe.unlink(missing_ok=True)
                continue

            log.info("Auto-update: download OK. Criando script de substituição…")

            # Cria script .bat que para serviço → substitui EXE → reinicia
            script = INSTALL_DIR / "bc_update.bat"
            script.write_text(
                "@echo off\n"
                "timeout /t 5 /nobreak >nul\n"
                f"net stop {SVC_NAME}\n"
                "timeout /t 2 /nobreak >nul\n"
                f'copy /y "{novo_exe}" "{exe_atual}"\n'
                f'del "{novo_exe}"\n'
                f"net start {SVC_NAME}\n"
                f'del "%~f0"\n',
                encoding="utf-8"
            )

            # Inicia o script desacoplado e encerra o agente
            subprocess.Popen(
                ["cmd", "/c", str(script)],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True
            )
            log.info("Auto-update: script iniciado. Encerrando agente para atualização.")
            sys.exit(0)

        except Exception as e:
            log.warning(f"Auto-update: erro ao verificar {base_url}: {e}")

    return False

# ─── Loop de coleta (compartilhado entre serviço e modo direto) ──────────────
def _loop_coleta(stop_event=None):
    """Executa o loop principal. stop_event: win32event handle ou None."""
    log.info("==========================================")
    log.info("  BlueControl IT - Agente v3")
    log.info(f"  Maquina: {socket.gethostname()}")
    log.info(f"  WMI: {'OK' if _WMI_OK else 'indisponivel'}")
    log.info("==========================================")

    global _AGENTE_HASH
    _AGENTE_HASH = _hash_self()
    if _AGENTE_HASH:
        log.info(f"  Hash do agente: {_AGENTE_HASH[:16]}...")
    else:
        log.info("  Hash do agente: indisponivel")

    _ultimo_inventario   = 0
    _ultimo_update_check = 0

    while True:
        # Verifica sinal de parada (quando rodando como serviço)
        if stop_event and _SVC_OK:
            if win32event.WaitForSingleObject(stop_event, 0) == win32event.WAIT_OBJECT_0:
                log.info("Servico: sinal de parada recebido.")
                break

        try:
            config = carregar_config()
            metricas = coletar_metricas()
            enviar_metricas(config, metricas)

            if time.time() - _ultimo_inventario >= 3600:
                inventario = coletar_inventario()
                if enviar_inventario(config, inventario):
                    _ultimo_inventario = time.time()
                eventos = coletar_eventos_log()
                enviar_eventos_log(config, eventos)

            # Verifica atualização a cada 1h (após envio de métricas bem-sucedido)
            if time.time() - _ultimo_update_check >= 3600:
                _ultimo_update_check = time.time()
                verificar_atualizacao(config)  # sys.exit() interno se atualizar

        except FileNotFoundError as e:
            log.error(str(e))
        except Exception as e:
            log.error(f"Erro inesperado: {e}")

        try:
            intervalo = carregar_config().get("intervalo_segundos", 300)
        except Exception:
            intervalo = 300

        # Aguarda em fatias de 5s para responder ao stop_event rapidamente
        fatias = max(1, intervalo // 5)
        for _ in range(fatias):
            if stop_event and _SVC_OK:
                if win32event.WaitForSingleObject(stop_event, 5000) == win32event.WAIT_OBJECT_0:
                    return
            else:
                time.sleep(5)


# ─── Classe de Serviço Windows ───────────────────────────────────────────────
if _SVC_OK:
    class BlueControlService(win32serviceutil.ServiceFramework):
        _svc_name_         = SVC_NAME
        _svc_display_name_ = SVC_DISPLAY
        _svc_description_  = SVC_DESC

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self._stop_event)

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, "")
            )
            _loop_coleta(self._stop_event)


# ─── Ponto de entrada ────────────────────────────────────────────────────────
if __name__ == "__main__":
    if _SVC_OK and len(sys.argv) > 1 and sys.argv[1] in ("install","remove","start","stop","restart","debug"):
        win32serviceutil.HandleCommandLine(BlueControlService)
    elif _SVC_OK:
        try:
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(BlueControlService)
            servicemanager.StartServiceCtrlDispatcher()
        except Exception:
            _loop_coleta()
    else:
        _loop_coleta()
