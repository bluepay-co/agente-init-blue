#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# BlueControl IT — Setup VPN macOS (LaunchDaemon, roda como root)
# Verifica se o perfil VPN_BLUEPAY existe e cria do zero se necessário.
# Roda uma vez por boot. Não faz conexão — isso é responsabilidade do
# LaunchAgent (agente_vpn.sh) que roda no contexto do usuário.
# ─────────────────────────────────────────────────────────────────────────────

VPN_NAME="VPN_BLUEPAY"
VPN_USER="daniela.farias"
VPN_PASS="a?P3W0{-a*98"
VPN_SERVIDOR="187.102.159.62"
LOG="/usr/local/bluecontrol/vpn_setup.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }

mkdir -p /usr/local/bluecontrol

log "=== BlueControl VPN Setup iniciado ==="

# Verifica se o perfil já existe
if scutil --nc list 2>/dev/null | grep -q "$VPN_NAME"; then
    log "Perfil $VPN_NAME ja existe — nenhuma acao necessaria"
    exit 0
fi

log "$VPN_NAME nao encontrado — criando perfil..."

python3 - <<PYEOF
import plistlib, uuid, sys

PLIST    = "/Library/Preferences/SystemConfiguration/preferences.plist"
VPN_NAME = "$VPN_NAME"
VPN_USER = "$VPN_USER"
VPN_PASS = "$VPN_PASS"
VPN_HOST = "$VPN_SERVIDOR"

try:
    with open(PLIST, "rb") as f:
        d = plistlib.load(f)
except Exception as e:
    print("ERRO ao ler plist: " + str(e))
    sys.exit(1)

services = d.setdefault("NetworkServices", {})

# Verificar novamente (corrida improvável entre processos)
for k, v in services.items():
    if v.get("UserDefinedName") == VPN_NAME:
        print("Perfil ja existe (UUID=" + k + ")")
        sys.exit(0)

new_uuid = str(uuid.uuid4()).upper()

vpn_config = {
    "DNS":  {},
    "IPv4": {"ConfigMethod": "PPP", "OverridePrimary": 1},
    "IPv6": {"ConfigMethod": "Automatic"},
    "Interface": {"SubType": "L2TP", "Type": "PPP"},
    "IPSec": {
        "AuthenticationMethod":  "SharedSecret",
        "LocalIdentifier":       "",
        "LocalIdentifierType":   "KeyID",
        # PSK passado via --secret no momento da conexão — não armazenado aqui
    },
    "PPP": {
        "AuthName":               VPN_USER,
        "AuthPassword":           VPN_PASS.encode(),
        "AuthPasswordEncryption": "Key",
        "CommRemoteAddress":      VPN_HOST,
        "TokenCard":              False,
        "CCPEnabled":             1,
        "IPCPCompressionVJ":      0,
        "ACSPEnabled":            1,
        "DisconnectOnSleep":      0,
    },
    "UserDefinedName": VPN_NAME,
    "VPN": {"VPNType": "L2TP"},
}

services[new_uuid] = vpn_config

# Adicionar ao Set ativo
current_set_path = d.get("CurrentSet", "")
if current_set_path:
    set_uuid = current_set_path.split("/")[-1]
    set_data = d.get("Sets", {}).get(set_uuid, {})
    net = set_data.setdefault("Network", {})
    svc_order = net.setdefault("Global", {}).setdefault("IPv4", {}).setdefault("ServiceOrder", [])
    if new_uuid not in svc_order:
        svc_order.insert(0, new_uuid)
    net.setdefault("Service", {})[new_uuid] = {"__LINK__": "/NetworkServices/" + new_uuid}

try:
    with open(PLIST, "wb") as f:
        plistlib.dump(d, f, fmt=plistlib.FMT_XML)
    print("Perfil VPN criado: UUID=" + new_uuid)
except Exception as e:
    print("ERRO ao salvar plist: " + str(e))
    sys.exit(1)
PYEOF

# Recarregar configurações de rede
killall -HUP configd 2>/dev/null || true
sleep 4
log "Configuracoes de rede recarregadas"
log "=== Setup concluido ==="
