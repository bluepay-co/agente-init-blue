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
SERVIDOR_IP="192.168.10.11"
SERVIDOR_PORTA="8550"
# Segredo vem do ambiente — nunca do código. Defina antes de rodar:
#   export BLUECONTROL_AGENT_TOKEN=...
AGENT_SECRET="${BLUECONTROL_AGENT_TOKEN:-}"
BASE_DIR="/usr/local/bluecontrol"
AGENTE_MTR="$BASE_DIR/BlueControl_Metricas_Mac"
PLIST_MTR="/Library/LaunchDaemons/com.bluecontrol.metricas.plist"
CONFIG="$BASE_DIR/config.json"
LOG="/tmp/bluecontrol_install.log"

if [ -z "$AGENT_SECRET" ]; then
    echo "[ERRO] BLUECONTROL_AGENT_TOKEN não definida. Exporte-a antes de rodar o instalador." >&2
    exit 1
fi

# ── Helpers de diálogo ───────────────────────────────────────
dialog_input() {
    osascript -e "display dialog \"$1\" default answer \"$2\" buttons {\"Cancelar\", \"OK\"} default button \"OK\" with title \"BlueControl IT — Instalador\"" \
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

echo ""
echo "Patrimônio: $PATRIMONIO"
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
log "Patrimônio: $PATRIMONIO"

# ── Diretório base ──────────────────────────────────────
mkdir -p "$BASE_DIR"
log "OK Diretório $BASE_DIR criado"

# ── SSH ─────────────────────────────────────────────────
systemsetup -setremotelogin on 2>/dev/null || true
launchctl unload /System/Library/LaunchDaemons/ssh.plist 2>/dev/null || true
launchctl load -w /System/Library/LaunchDaemons/ssh.plist 2>/dev/null || true
log "OK SSH habilitado"

# ── Config JSON ──────────────────────────────────────────
cat > "$CONFIG" << CFGEOF
{
  "patrimonio": "$PATRIMONIO",
  "servidor_ip": "$SERVIDOR_IP",
  "servidor_porta": $SERVIDOR_PORTA,
  "agent_secret": "$AGENT_SECRET",
  "intervalo_metricas_min": 5,
  "intervalo_inventario_min": 60,
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
    dialog_info "✅ Instalação concluída com sucesso!\n\n• Patrimônio: $PATRIMONIO\n• SSH ativo\n• Agente Métricas — envia dados a cada 5 min\n\nLog em: $LOG" "Sucesso"
else
    log "ERRO: $RESULTADO"
    dialog_erro "Erro durante instalação:\n$RESULTADO"
    exit 1
fi
