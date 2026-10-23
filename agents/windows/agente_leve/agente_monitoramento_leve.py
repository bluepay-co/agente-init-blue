"""
BlueControl IT — Agente de Monitoramento Leve v1
=================================================
Agente minimalista que coleta apenas métricas essenciais.
Instalação como Serviço Windows — sem WMI, sem dependências pesadas.

Dependências:
  pip install psutil requests

Compilar:
  pyinstaller --onefile --noconsole --add-data "config.json:."
    --name "BlueControl_Lite" agente_monitoramento_leve.py

Config: C:\\BlueControl\\config.json
Log: C:\\BlueControl\\lite.log
"""

import os
import sys
import json
import time
import socket
import logging
import requests
from pathlib import Path
from datetime import datetime

try:
    import servicemanager
    import win32service
    import win32serviceutil
    import win32event
    _WINDOWS_SVC = True
except ImportError:
    _WINDOWS_SVC = False

try:
    import psutil
except ImportError:
    print("ERRO: psutil não instalado. Execute: pip install psutil requests")
    sys.exit(1)

# ───────────────────────────────────────────────────────────────────────────
# CAMINHOS E CONFIGURAÇÃO
# ───────────────────────────────────────────────────────────────────────────
CONFIG_PATH = Path(r"C:\BlueControl\config.json")
LOG_PATH    = Path(r"C:\BlueControl\lite.log")
INSTALL_DIR = Path(r"C:\BlueControl")

# ───────────────────────────────────────────────────────────────────────────
# LOGGING
# ───────────────────────────────────────────────────────────────────────────
os.makedirs(str(INSTALL_DIR), exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("bluecontrol_lite")

# ───────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ───────────────────────────────────────────────────────────────────────────
def carregar_config():
    """Carrega config.json com valores padrão."""
    if not CONFIG_PATH.exists():
        # Cria config padrão se não existir
        config_padrao = {
            "patrimonio": "0001",
            "url_api": "http://192.168.0.17:8550",
            "token_agente": os.environ.get("BLUECONTROL_AGENT_TOKEN", ""),
            "intervalo_segundos": 300  # 5 minutos
        }
        CONFIG_PATH.write_text(json.dumps(config_padrao, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"Config padrão criado em {CONFIG_PATH}")
        return config_padrao

    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        # Garante valores padrão
        cfg.setdefault("intervalo_segundos", 300)
        # O token não tem padrão: um valor conhecido no código-fonte vale tanto
        # quanto nenhum. Sem ele o agente para em vez de falar com a API aberto.
        if not cfg.get("token_agente"):
            log.error(f"'token_agente' ausente ou vazio em {CONFIG_PATH} — preencha antes de iniciar")
            return None
        return cfg
    except Exception as e:
        log.error(f"Erro ao carregar config: {e}")
        return None

# ───────────────────────────────────────────────────────────────────────────
# COLETA DE MÉTRICAS (LEVE)
# ───────────────────────────────────────────────────────────────────────────
def coletar_metricas():
    """Coleta apenas as métricas essenciais (rápido e leve)."""
    try:
        # CPU
        cpu_percent = round(psutil.cpu_percent(interval=1), 1)

        # RAM
        ram = psutil.virtual_memory()
        ram_percent = round(ram.percent, 1)
        ram_total_gb = round(ram.total / (1024**3), 1)

        # Disco C:
        try:
            disco = psutil.disk_usage("C:\\")
            disco_percent = round(disco.percent, 1)
            disco_total_gb = round(disco.total / (1024**3), 1)
        except Exception:
            disco_percent = None
            disco_total_gb = None

        # IPs
        ip_local = None
        ip_vpn = None
        try:
            for iface, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        ip = addr.address
                        if ip.startswith(('172.16.', '172.17.', '10.')):
                            ip_vpn = ip
                        elif ip.startswith('192.168.') and ip != '127.0.0.1':
                            ip_local = ip
        except Exception as e:
            log.warning(f"Erro ao detectar IPs: {e}")

        # Hostname
        hostname = socket.gethostname()

        # Uptime
        try:
            uptime_horas = round((time.time() - psutil.boot_time()) / 3600, 2)
        except Exception:
            uptime_horas = None

        metricas = {
            "tipo_os": "windows",
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "ram_total_gb": ram_total_gb,
            "disco_percent": disco_percent,
            "disco_total_gb": disco_total_gb,
            "ip_local": ip_local,
            "ip_vpn": ip_vpn,
            "hostname": hostname,
            "uptime_horas": uptime_horas,
        }

        return metricas

    except Exception as e:
        log.error(f"Erro ao coletar métricas: {e}")
        return None

# ───────────────────────────────────────────────────────────────────────────
# ENVIO DAS MÉTRICAS
# ───────────────────────────────────────────────────────────────────────────
def enviar_metricas(config, metricas):
    """Envia métricas para a API."""
    if not metricas or not config:
        return False

    try:
        url = f"{config['url_api']}/api/noc/maquinas/{config['patrimonio']}/metricas"
        headers = {
            "X-Agent-Token": config["token_agente"],
            "Content-Type": "application/json"
        }

        response = requests.post(url, json=metricas, headers=headers, timeout=10)

        if response.status_code == 200:
            log.info(f"✓ Métricas enviadas com sucesso | CPU: {metricas['cpu_percent']}% | RAM: {metricas['ram_percent']}%")
            return True
        else:
            log.warning(f"Erro ao enviar métricas (HTTP {response.status_code}): {response.text[:100]}")
            return False

    except requests.exceptions.ConnectionError:
        log.warning(f"Conexão recusada: {config['url_api']} — API offline?")
        return False
    except requests.exceptions.Timeout:
        log.warning(f"Timeout ao conectar em {config['url_api']}")
        return False
    except Exception as e:
        log.error(f"Erro ao enviar métricas: {e}")
        return False

# ───────────────────────────────────────────────────────────────────────────
# LOOP PRINCIPAL (Modo Independente)
# ───────────────────────────────────────────────────────────────────────────
def loop_principal(config):
    """Loop contínuo que coleta e envia métricas."""
    intervalo = config.get("intervalo_segundos", 300)
    log.info(f"[INICIADO] BlueControl Lite | Patrimônio: {config['patrimonio']} | API: {config['url_api']}")
    log.info(f"Intervalo: {intervalo}s | Primeira coleta em {intervalo}s...")

    while True:
        try:
            time.sleep(intervalo)

            metricas = coletar_metricas()
            if metricas:
                enviar_metricas(config, metricas)

        except KeyboardInterrupt:
            log.info("[PARADO] Agente interrompido pelo usuário")
            break
        except Exception as e:
            log.error(f"Erro no loop principal: {e}")
            time.sleep(60)  # Aguarda 1 min antes de tentar novamente

# ───────────────────────────────────────────────────────────────────────────
# SERVIÇO WINDOWS (Opcional)
# ───────────────────────────────────────────────────────────────────────────
if _WINDOWS_SVC:
    class BlueControlLiteService(win32serviceutil.ServiceFramework):
        _svc_name_ = "BlueControlLite"
        _svc_display_name_ = "BlueControl IT - Agente Leve"
        _svc_description_ = "Agente minimalista de monitoramento para BlueControl IT"

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.is_alive = True

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.stop_event)
            self.is_alive = False

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, "")
            )

            config = carregar_config()
            if not config:
                log.error("Falha ao carregar configuração. Serviço abortado.")
                return

            intervalo = config.get("intervalo_segundos", 300)
            log.info(f"[SVC INICIADO] {self._svc_name_}")

            while self.is_alive:
                try:
                    metricas = coletar_metricas()
                    if metricas:
                        enviar_metricas(config, metricas)

                    # Aguarda próxima coleta (com possibilidade de parada)
                    if win32event.WaitForSingleObject(self.stop_event, intervalo * 1000) == win32event.WAIT_OBJECT_0:
                        break

                except Exception as e:
                    log.error(f"Erro no serviço: {e}")
                    time.sleep(60)

            log.info("[SVC PARADO]")

# ───────────────────────────────────────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    config = carregar_config()

    if not config:
        print("ERRO: Falha ao carregar configuração")
        sys.exit(1)

    # Se for executável como serviço
    if _WINDOWS_SVC and len(sys.argv) > 1:
        win32serviceutil.HandleCommandLine(BlueControlLiteService)
    else:
        # Modo debug/independente
        print(f"BlueControl Lite — Agente de Monitoramento")
        print(f"Patrimônio: {config['patrimonio']}")
        print(f"API: {config['url_api']}")
        print(f"Intervalo: {config.get('intervalo_segundos', 300)}s")
        print(f"Config: {CONFIG_PATH}")
        print(f"Log: {LOG_PATH}")
        print("\nIniciando coleta de métricas (pressione Ctrl+C para sair)...\n")

        loop_principal(config)
