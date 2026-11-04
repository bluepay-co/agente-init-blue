#!/usr/bin/env bash
# ============================================================
# BlueControl IT — Build do instalador macOS (.app)
# ============================================================
# RODE ESTE SCRIPT NUM MAC. Binário Mach-O do macOS não se
# compila no Linux/Wine — precisa do próprio macOS + PyInstaller.
#
# O que ele gera (em dist/):
#   1. BlueControl_Metricas_Mac      — agente de métricas (onefile)
#   2. BlueControl_Agente_Mac.app    — instalador gráfico, com o
#      agente de métricas embutido (autossuficiente, igual ao .exe
#      do Windows que embute o BlueControl_Metricas.exe)
#
# Pré-requisitos no Mac (uma vez):
#   - Xcode Command Line Tools:  xcode-select --install
#   - Python 3:                  brew install python  (ou python.org)
#   - Dependências:
#       python3 -m pip install --upgrade pip
#       python3 -m pip install pyinstaller pywebview pyobjc psutil requests
#
# Uso:
#   chmod +x compilar_mac.sh
#   ./compilar_mac.sh
#
# Ícone (opcional): coloque um Bluepay.icns nesta pasta e ele é usado.
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "ERRO: este build só roda no macOS (uname=$(uname))." >&2
    echo "Binário do Mac não pode ser gerado no Linux/Wine." >&2
    exit 1
fi

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "ERRO: python3 não encontrado no PATH." >&2; exit 1; }
"$PY" -m PyInstaller --version >/dev/null 2>&1 || { echo "ERRO: PyInstaller não instalado (veja o cabeçalho)." >&2; exit 1; }

ICONE=()
[[ -f "Bluepay.icns" ]] && ICONE=(--icon "Bluepay.icns")

echo "[1/2] Compilando o agente de métricas (BlueControl_Metricas_Mac)..."
rm -rf build/metricas dist/BlueControl_Metricas_Mac
"$PY" -m PyInstaller \
    --onefile \
    --name "BlueControl_Metricas_Mac" \
    --workpath build/metricas \
    --specpath build \
    agente_metricas_mac.py

if [[ ! -f dist/BlueControl_Metricas_Mac ]]; then
    echo "ERRO: o agente de métricas não foi gerado em dist/." >&2
    exit 1
fi

echo "[2/2] Compilando o instalador (.app) com o agente embutido..."
rm -rf build/instalador "dist/BlueControl_Agente_Mac.app"
# --windowed → gera .app (sem terminal); --add-data embute o agente de métricas
# (no macOS o separador do --add-data é ":").
# --collect-all webview: sem isso o pywebview não é empacotado e o .app não
#   abre (exigência da própria pywebview para PyInstaller).
# --hidden-import webview.platforms.cocoa: backend do macOS, importado só em
#   runtime — a análise estática do PyInstaller não o vê sozinha.
"$PY" -m PyInstaller \
    --windowed \
    --name "BlueControl_Agente_Mac" \
    --collect-all webview \
    --hidden-import webview.platforms.cocoa \
    --add-data "$PWD/dist/BlueControl_Metricas_Mac:." \
    --workpath build/instalador \
    --specpath build \
    ${ICONE[@]+"${ICONE[@]}"} \
    instalar_bluecontrol_mac.py

echo
if [[ -d "dist/BlueControl_Agente_Mac.app" ]]; then
    echo "✓ Gerado: dist/BlueControl_Agente_Mac.app"
    echo
    echo "DISTRIBUIÇÃO / GATEKEEPER:"
    echo "  O app não é assinado/notarizado — no primeiro uso o macOS bloqueia."
    echo "  Na máquina de destino, o técnico abre com botão direito → Abrir,"
    echo "  ou remove a quarentena antes:"
    echo "      xattr -dr com.apple.quarantine \"/caminho/BlueControl_Agente_Mac.app\""
    echo "  A instalação pede a senha de administrador do macOS (osascript)."
else
    echo "✗ O .app não apareceu em dist/ — confira o log acima." >&2
    exit 1
fi
