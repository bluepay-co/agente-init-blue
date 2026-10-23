#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# BlueControl IT — Agente VPN macOS
# Roda como LaunchAgent do usuário (gui/<uid>) a cada 60 segundos.
# • Fora da rede Bluepay → conecta VPN
# • Na rede Bluepay      → desconecta VPN se ativa
# ─────────────────────────────────────────────────────────────────────────────

VPN_NAME="VPN_BLUEPAY"
VPN_USER=""
VPN_PASS=""
VPN_PSK=""

# Carrega credenciais específicas da máquina (gerado pelo instalador).
# A PSK vive só aqui: fora do código e fora do Git.
[ -f /usr/local/bluecontrol/vpn_config.sh ] && source /usr/local/bluecontrol/vpn_config.sh

VPN_SERVIDOR="187.102.159.62"
SERVIDOR_BC="192.168.0.17"
SSID_EMPRESA="Bluepay Corp"
GATEWAY_EMPRESA="192.168.1.254"
COOLDOWN_FILE="/usr/local/bluecontrol/vpn_cooldown"
COOLDOWN_SEG=70
LOG="/usr/local/bluecontrol/agente.log"
LOCK_FILE="/tmp/bluecontrol_vpn.lock"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }

# Sem a PSK o agente não tem o que fazer: para em vez de tentar conectar.
if [ -z "$VPN_PSK" ]; then
    log "ERRO VPN_PSK ausente em /usr/local/bluecontrol/vpn_config.sh"
    exit 1
fi

# ─── Lock: apenas uma instância por vez ──────────────────────────────────────
if [ -f "$LOCK_FILE" ]; then
    OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null)
    if kill -0 "$OLD_PID" 2>/dev/null; then
        exit 0
    fi
fi
echo $$ > "$LOCK_FILE"
trap "rm -f '$LOCK_FILE'" EXIT INT TERM

# ─── Funções auxiliares ───────────────────────────────────────────────────────
garantir_vpn_configurada() {
    if scutil --nc list 2>/dev/null | grep -q "$VPN_NAME"; then
        return 0
    fi
    log "AVISO: perfil $VPN_NAME nao encontrado."
    return 1
}

esta_no_escritorio() {
    local ssid
    ssid=$(networksetup -getairportnetwork en0 2>/dev/null | awk -F': ' '{print $2}')
    [ "$ssid" = "$SSID_EMPRESA" ] && return 0
    local gw
    gw=$(route -n get default 2>/dev/null | grep gateway | awk '{print $2}')
    [ "$gw" = "$GATEWAY_EMPRESA" ] && return 0
    return 1
}

vpn_conectada() {
    scutil --nc status "$VPN_NAME" 2>/dev/null | grep -q "Connected"
}

em_cooldown() {
    [ -f "$COOLDOWN_FILE" ] || return 1
    local ultima agora
    ultima=$(cat "$COOLDOWN_FILE")
    agora=$(date +%s)
    [ $((agora - ultima)) -lt $COOLDOWN_SEG ]
}

registrar_cooldown() { date +%s > "$COOLDOWN_FILE"; }

garantir_rota_interna() {
    route -n get 192.168.0.17 2>/dev/null | grep -q "ppp" && return 0
    sudo route add -net 192.168.0.0/24 -interface ppp0 2>/dev/null || true
}

# ─── Lógica principal ─────────────────────────────────────────────────────────
garantir_vpn_configurada || exit 0

if esta_no_escritorio; then
    if vpn_conectada; then
        log "No escritorio — desconectando VPN"
        scutil --nc stop "$VPN_NAME" 2>/dev/null
        registrar_cooldown
    fi
else
    if vpn_conectada; then
        garantir_rota_interna
        ping -c 1 -W 3 "$SERVIDOR_BC"  >/dev/null 2>&1 || \
        ping -c 1 -W 3 "$VPN_SERVIDOR" >/dev/null 2>&1 || true
    else
        if ! em_cooldown; then
            log "Fora da rede — conectando VPN"
            scutil --nc start "$VPN_NAME" \
                --user "$VPN_USER" \
                --password "$VPN_PASS" \
                --secret "$VPN_PSK" \
                2>/dev/null
            sleep 20
            if vpn_conectada; then
                log "VPN conectada com sucesso"
                rm -f "$COOLDOWN_FILE"
                garantir_rota_interna
            else
                log "Falha ao conectar VPN"
                registrar_cooldown
            fi
        fi
    fi
fi
