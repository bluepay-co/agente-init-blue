"""
BlueControl IT — Agente VPN Windows (Serviço) — detecção por MAC do gateway
=============================================================================
Identifica o escritório pelo MAC address do roteador padrão.
Isso resolve ambiguidade de SSID igual em filiais e gateway IP igual em casas.

Compilar:
  pyinstaller --onefile --noconsole
    --hidden-import=win32service --hidden-import=win32serviceutil
    --hidden-import=win32event --hidden-import=servicemanager
    --name "BlueControl_VPN" agente_vpn_windows.py
"""

import os, sys, time, subprocess, logging, json
from pathlib import Path

import servicemanager
import win32service
import win32serviceutil
import win32event

# ─── Caminhos ────────────────────────────────────────────────────────────────
CONFIG_PATH   = Path(r"C:\BlueControl\config.json")
LOG_PATH      = Path(r"C:\BlueControl\vpn.log")
COOLDOWN_FILE = Path(r"C:\BlueControl\vpn_cooldown.txt")
INSTALL_DIR   = Path(r"C:\BlueControl")
SVC_NAME      = "BlueControlVPN"
SVC_DISPLAY   = "BlueControl IT - Agente VPN"
SVC_DESC      = "Conecta e desconecta a VPN BluePay automaticamente conforme a rede detectada."

# ─── Logging ─────────────────────────────────────────────────────────────────
os.makedirs(str(INSTALL_DIR), exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH), level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("vpn")

# ─── Config ──────────────────────────────────────────────────────────────────
def carregar_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config nao encontrado: {CONFIG_PATH}")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

# ─── Helpers PowerShell ──────────────────────────────────────────────────────
def _ps(cmd: str, timeout: int = 10) -> str:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip()
    except Exception:
        return ""

# ─── Detecção de rede por MAC do gateway ─────────────────────────────────────
def _gateway_ip() -> str:
    return _ps(
        "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue"
        " | Sort-Object RouteMetric | Select-Object -First 1).NextHop"
    )

def _mac_do_gateway(gw_ip: str) -> str:
    """Retorna o MAC address do gateway, normalizado em maiúsculas com ':'."""
    if not gw_ip:
        return ""
    # Garante entrada na tabela ARP
    _ps(f"ping -n 1 -w 800 {gw_ip} | Out-Null", timeout=5)
    mac = _ps(
        f"(Get-NetNeighbor -IPAddress '{gw_ip}' -ErrorAction SilentlyContinue"
        f" | Where-Object {{$_.State -ne 'Unreachable'}} | Select-Object -First 1).LinkLayerAddress"
    )
    return mac.upper().replace("-", ":").strip() if mac else ""

def _norm_mac(mac: str) -> str:
    return mac.upper().replace("-", ":").strip()

def esta_no_escritorio(config: dict) -> bool:
    """
    Compara o MAC do gateway atual com o(s) MAC(s) gravados na instalação.
    Fallback para gateway IP se gateway_macs_empresa não estiver no config.
    """
    macs_empresa = [_norm_mac(m) for m in config.get("gateway_macs_empresa", []) if m]

    if not macs_empresa:
        # Fallback legado: compara por IP de gateway
        gateways = config.get("gateways_empresa", ["192.168.1.254", "192.168.0.1"])
        gw = _gateway_ip()
        if gw in gateways:
            log.debug(f"Escritorio via gateway IP (fallback): {gw}")
            return True
        return False

    gw_ip  = _gateway_ip()
    mac    = _mac_do_gateway(gw_ip)

    if not mac:
        log.debug("MAC do gateway indisponivel — assumindo fora do escritorio.")
        return False

    if mac in macs_empresa:
        log.debug(f"Escritorio detectado via MAC do gateway: {mac}")
        return True

    log.debug(f"MAC {mac} nao reconhecido (empresa: {macs_empresa}) — fora do escritorio.")
    return False

# ─── VPN ─────────────────────────────────────────────────────────────────────
def vpn_conectada(vpn_name: str) -> bool:
    status = _ps(
        f"(Get-VpnConnection -Name '{vpn_name}' -AllUserConnection"
        f" -ErrorAction SilentlyContinue).ConnectionStatus"
    )
    return status == "Connected"

def em_cooldown() -> bool:
    if COOLDOWN_FILE.exists():
        try:
            if time.time() < float(COOLDOWN_FILE.read_text().strip()) + 70:
                return True
        except Exception:
            pass
    return False

def registrar_cooldown():
    COOLDOWN_FILE.write_text(str(time.time()))

def conectar_vpn(vpn_name: str, usuario: str, senha: str):
    log.info(f"Conectando VPN '{vpn_name}' para {usuario}...")
    r = subprocess.run(
        ["rasdial", vpn_name, usuario, senha],
        capture_output=True, text=True, timeout=30
    )
    if r.returncode == 0:
        log.info("VPN conectada com sucesso.")
    else:
        log.warning(f"Falha ao conectar VPN: {r.stdout.strip()} {r.stderr.strip()}")
        registrar_cooldown()

def desconectar_vpn(vpn_name: str):
    log.info(f"Desconectando VPN '{vpn_name}'...")
    subprocess.run(["rasdial", vpn_name, "/disconnect"], capture_output=True, timeout=15)
    registrar_cooldown()

# ─── Loop principal ──────────────────────────────────────────────────────────
def _loop_vpn(stop_event=None):
    log.info("=== BlueControl IT - Agente VPN (deteccao por MAC) iniciado ===")

    while True:
        if stop_event:
            if win32event.WaitForSingleObject(stop_event, 0) == win32event.WAIT_OBJECT_0:
                log.info("Servico: sinal de parada recebido.")
                break

        try:
            config   = carregar_config()
            vpn_name = config.get("vpn_nome", "VPN_BLUEPAY")
            usuario  = config.get("vpn_usuario", "")
            senha    = config.get("vpn_senha", "")

            if not usuario or not senha:
                log.warning("vpn_usuario ou vpn_senha nao configurados no config.json")
            else:
                no_escritorio = esta_no_escritorio(config)
                conectada     = vpn_conectada(vpn_name)

                if no_escritorio:
                    if conectada:
                        log.info("Rede interna detectada (MAC) — desconectando VPN.")
                        desconectar_vpn(vpn_name)
                else:
                    if not conectada and not em_cooldown():
                        conectar_vpn(vpn_name, usuario, senha)

        except FileNotFoundError as e:
            log.error(str(e))
        except Exception as e:
            log.error(f"Erro inesperado: {e}")

        for _ in range(12):
            if stop_event:
                if win32event.WaitForSingleObject(stop_event, 5000) == win32event.WAIT_OBJECT_0:
                    return
            else:
                time.sleep(5)


# ─── Classe de Serviço Windows ───────────────────────────────────────────────
class BlueControlVPNService(win32serviceutil.ServiceFramework):
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
        _loop_vpn(self._stop_event)


# ─── Ponto de entrada ────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("install","remove","start","stop","restart","debug"):
        win32serviceutil.HandleCommandLine(BlueControlVPNService)
    else:
        try:
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(BlueControlVPNService)
            servicemanager.StartServiceCtrlDispatcher()
        except Exception:
            _loop_vpn()
