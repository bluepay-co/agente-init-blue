#!/bin/bash
# ============================================================
# BlueControl IT — Instalador do Agente para MacBook
# Usa diálogos nativos do macOS (osascript) — sem dependências
# ============================================================
# Uso:
#   bash instalar_bluecontrol_mac.sh
# ============================================================

set -e

# ── Configurações fixas ──────────────────────────────────────
SERVIDOR_IP="192.168.0.17"
SERVIDOR_PORTA="8550"
# Segredos vêm do ambiente — nunca do código. Defina antes de rodar:
#   export BLUECONTROL_AGENT_TOKEN=... BLUECONTROL_VPN_PSK=...
AGENT_SECRET="${BLUECONTROL_AGENT_TOKEN:-}"
VPN_NAME="VPN_BLUEPAY"
VPN_SERVIDOR="187.102.159.62"
VPN_PSK="${BLUECONTROL_VPN_PSK:-}"
SSID_EMPRESA="Bluepay Corp"
BASE_DIR="/usr/local/bluecontrol"
AGENTE_VPN="$BASE_DIR/agente_vpn.sh"
AGENTE_MTR="$BASE_DIR/BlueControl_Metricas_Mac"
PLIST_VPN="/Library/LaunchDaemons/com.bluecontrol.agente.plist"
PLIST_MTR="/Library/LaunchDaemons/com.bluecontrol.metricas.plist"
CONFIG="$BASE_DIR/config.json"
LOG="/tmp/bluecontrol_install.log"

for _var in AGENT_SECRET:BLUECONTROL_AGENT_TOKEN VPN_PSK:BLUECONTROL_VPN_PSK; do
    _nome="${_var%%:*}"; _env="${_var##*:}"
    if [ -z "${!_nome}" ]; then
        echo "[ERRO] $_env não definida. Exporte-a antes de rodar o instalador." >&2
        exit 1
    fi
done

# ── Helpers de diálogo ───────────────────────────────────────
dialog_input() {
    osascript -e "display dialog \"$1\" default answer \"$2\" buttons {\"Cancelar\", \"OK\"} default button \"OK\" with title \"BlueControl IT — Instalador\"" \
              -e "text returned of result" 2>/dev/null
}

dialog_pass() {
    osascript -e "display dialog \"$1\" default answer \"\" with hidden answer buttons {\"Cancelar\", \"OK\"} default button \"OK\" with title \"BlueControl IT — Instalador\"" \
              -e "text returned of result" 2>/dev/null
}

dialog_info() {
    osascript -e "display notification \"$1\" with title \"BlueControl IT\" subtitle \"$2\"" 2>/dev/null || true
    osascript -e "display dialog \"$1\" buttons {\"OK\"} default button \"OK\" with title \"BlueControl IT — Instalador\"" 2>/dev/null || true
}

dialog_erro() {
    osascript -e "display dialog \"❌ $1\" buttons {\"OK\"} default button \"OK\" with title \"BlueControl IT — Erro\" with icon stop" 2>/dev/null || true
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

# ── Coleta dados via diálogos nativos ────────────────────────
echo "=== BlueControl IT — Instalador Mac ==="
echo "Servidor: $SERVIDOR_IP"

PATRIMONIO=$(dialog_input "Patrimônio do equipamento:\n(ex: 0065 ou PER-0010)" "")
if [ -z "$PATRIMONIO" ]; then
    dialog_erro "Patrimônio não informado. Instalação cancelada."
    exit 1
fi
PATRIMONIO=$(echo "$PATRIMONIO" | tr '[:lower:]' '[:upper:]')

VPN_USER=$(dialog_input "Usuário VPN do colaborador:\n(ex: nome.sobrenome)" "")
if [ -z "$VPN_USER" ]; then
    dialog_erro "Usuário VPN não informado. Instalação cancelada."
    exit 1
fi

VPN_PASS=$(dialog_pass "Senha VPN do colaborador:")
if [ -z "$VPN_PASS" ]; then
    dialog_erro "Senha VPN não informada. Instalação cancelada."
    exit 1
fi

echo ""
echo "Patrimônio: $PATRIMONIO"
echo "Usuário VPN: $VPN_USER"
echo "Iniciando instalação..."

# ── Script privilegiado ──────────────────────────────────────
# Localiza o agente de métricas (mesma pasta do script)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
EXE_MTR="$SCRIPT_DIR/BlueControl_Metricas_Mac"

# Monta script de instalação com privilégios
INSTALL_SCRIPT=$(cat << SCRIPTEOF
#!/bin/bash
set -e
LOG="$LOG"
log() { echo "[\\$(date '+%Y-%m-%d %H:%M:%S')] \\$1" >> "\\$LOG"; }

log "=== Iniciando instalação BlueControl IT ==="
log "Patrimônio: $PATRIMONIO | Usuário: $VPN_USER"

# ── Diretório base ──────────────────────────────────────
mkdir -p "$BASE_DIR"
log "OK Diretório $BASE_DIR criado"

# ── SSH ─────────────────────────────────────────────────
systemsetup -setremotelogin on 2>/dev/null || true
launchctl unload /System/Library/LaunchDaemons/ssh.plist 2>/dev/null || true
launchctl load -w /System/Library/LaunchDaemons/ssh.plist 2>/dev/null || true
log "OK SSH habilitado"

# ── VPN L2TP/IPSec ──────────────────────────────────────
networksetup -deletepppoeservice "$VPN_NAME" 2>/dev/null || true
sleep 1
networksetup -createnetworkservice "$VPN_NAME" L2TP 2>/dev/null || true
networksetup -setremoteaddress "$VPN_NAME" "$VPN_SERVIDOR" 2>/dev/null || true
networksetup -setpppoeaccountname "$VPN_NAME" "$VPN_USER" 2>/dev/null || true
networksetup -setpppoepassword "$VPN_NAME" "$VPN_PASS" 2>/dev/null || true
log "OK VPN $VPN_NAME configurada"

# ── Agente VPN (shell script) ────────────────────────────
cat > "$AGENTE_VPN" << 'AGENTEOF'
#!/bin/bash
VPN_NAME="VPN_BLUEPAY"
VPN_USER="$VPN_USER"
VPN_PASS="$VPN_PASS"
SSID_EMPRESA="$SSID_EMPRESA"
COOLDOWN_FILE="/usr/local/bluecontrol/vpn_cooldown"
COOLDOWN_SEG=70
LOG="/usr/local/bluecontrol/agente.log"
log() { echo "[\$(date '+%Y-%m-%d %H:%M:%S')] \$1" >> "\$LOG"; }
esta_no_escritorio() {
    local ssid gw
    ssid=\$(networksetup -getairportnetwork en0 2>/dev/null | awk -F': ' '{print \$2}')
    [ "\$ssid" = "\$SSID_EMPRESA" ] && return 0
    gw=\$(route -n get default 2>/dev/null | grep gateway | awk '{print \$2}')
    [ "\$gw" = "192.168.1.254" ] || [ "\$gw" = "192.168.0.1" ] && return 0
    return 1
}
vpn_conectada() { scutil --nc status "\$VPN_NAME" 2>/dev/null | grep -q "Connected"; }
em_cooldown() {
    [ -f "\$COOLDOWN_FILE" ] || return 1
    local u a; u=\$(cat "\$COOLDOWN_FILE"); a=\$(date +%s)
    [ \$((a - u)) -lt \$COOLDOWN_SEG ]
}
registrar_cooldown() { date +%s > "\$COOLDOWN_FILE"; }
if esta_no_escritorio; then
    vpn_conectada && { log "No escritorio — desconectando"; scutil --nc stop "\$VPN_NAME" 2>/dev/null; registrar_cooldown; }
else
    if ! vpn_conectada && ! em_cooldown; then
        log "Fora da rede — conectando VPN"
        scutil --nc start "\$VPN_NAME" --user "\$VPN_USER" --password "\$VPN_PASS" 2>/dev/null
        sleep 5
        vpn_conectada && log "VPN conectada" || { log "Falha ao conectar"; registrar_cooldown; }
    fi
fi
AGENTEOF
chmod +x "$AGENTE_VPN"
log "OK Agente VPN instalado"

# ── LaunchDaemon VPN (1 min) ─────────────────────────────
cat > "$PLIST_VPN" << 'PLISTEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.bluecontrol.agente</string>
    <key>ProgramArguments</key><array><string>/bin/bash</string><string>/usr/local/bluecontrol/agente_vpn.sh</string></array>
    <key>StartInterval</key><integer>60</integer>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
    <key>StandardOutPath</key><string>/usr/local/bluecontrol/agente_out.log</string>
    <key>StandardErrorPath</key><string>/usr/local/bluecontrol/agente_err.log</string>
</dict></plist>
PLISTEOF
chown root:wheel "$PLIST_VPN"
chmod 644 "$PLIST_VPN"
launchctl unload "$PLIST_VPN" 2>/dev/null || true
launchctl load -w "$PLIST_VPN"
log "OK LaunchDaemon VPN instalado"

# ── Config JSON ──────────────────────────────────────────
cat > "$CONFIG" << CFGEOF
{
  "patrimonio": "$PATRIMONIO",
  "servidor_ip": "$SERVIDOR_IP",
  "servidor_porta": $SERVIDOR_PORTA,
  "agent_secret": "$AGENT_SECRET",
  "intervalo_metricas_min": 5,
  "intervalo_inventario_min": 60,
  "vpn_usuario": "$VPN_USER",
  "tipo_os": "macos"
}
CFGEOF
chmod 600 "$CONFIG"
log "OK Config salvo ($PATRIMONIO)"

# ── Agente de Métricas ───────────────────────────────────
if [ -f "$EXE_MTR" ]; then
    cp "$EXE_MTR" "$AGENTE_MTR"
    chmod +x "$AGENTE_MTR"
    cat > "$PLIST_MTR" << 'PLISTEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.bluecontrol.metricas</string>
    <key>ProgramArguments</key><array><string>/usr/local/bluecontrol/BlueControl_Metricas_Mac</string></array>
    <key>StartInterval</key><integer>300</integer>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
    <key>StandardOutPath</key><string>/usr/local/bluecontrol/metricas_out.log</string>
    <key>StandardErrorPath</key><string>/usr/local/bluecontrol/metricas_err.log</string>
    <key>EnvironmentVariables</key><dict><key>HOME</key><string>/var/root</string></dict>
</dict></plist>
PLISTEOF
    chown root:wheel "$PLIST_MTR"
    chmod 644 "$PLIST_MTR"
    launchctl unload "$PLIST_MTR" 2>/dev/null || true
    launchctl load -w "$PLIST_MTR"
    log "OK Agente de Métricas instalado"
else
    log "AVISO: BlueControl_Metricas_Mac não encontrado — métricas não instaladas"
fi

# ── Dispara agente VPN imediatamente ────────────────────
/bin/bash "$AGENTE_VPN" &
log "=== Instalação concluída ==="
echo "INSTALACAO_OK"
SCRIPTEOF
)

# ── Executa com privilégios via osascript ────────────────────
echo "$INSTALL_SCRIPT" > /tmp/bluecontrol_install.sh
chmod +x /tmp/bluecontrol_install.sh

log "Solicitando privilégios de administrador..."
RESULTADO=$(osascript -e 'do shell script "bash /tmp/bluecontrol_install.sh" with administrator privileges' 2>&1)

if echo "$RESULTADO" | grep -q "INSTALACAO_OK"; then
    log "Instalação concluída com sucesso!"
    dialog_info "✅ Instalação concluída com sucesso!\n\n• Patrimônio: $PATRIMONIO\n• SSH ativo\n• VPN BluePay configurada\n• Agente VPN — reconecta automaticamente\n• Agente Métricas — envia dados a cada 5 min\n\nLog em: $LOG" "Sucesso"
else
    log "ERRO: $RESULTADO"
    dialog_erro "Erro durante instalação:\n$RESULTADO"
    exit 1
fi
