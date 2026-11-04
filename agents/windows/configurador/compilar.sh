#!/usr/bin/env bash
# Compila o Configurador do Agente pra .exe, via Wine (mesmo prefixo já
# usado pro Agente Blue: ~/.wine64-agente, com Python 3.11 + PyInstaller).
#
# Pré-requisito: rode ../compilar_metricas.sh primeiro pra gerar o
# BlueControl_Metricas.exe (o agente completo) — este script embute ele junto.
#
# Uso:
#   ./compilar.sh
#
# Saída: dist/ConfiguradorAgente.exe (arquivo único — HTML/CSS/JS e o
# agente já compilado ficam embutidos dentro do .exe).

set -euo pipefail
cd "$(dirname "$0")"

WINEPREFIX="$HOME/.wine64-agente"
PYTHON="$WINEPREFIX/drive_c/users/$USER/AppData/Local/Programs/Python/Python311/python.exe"

if [ ! -f "$PYTHON" ]; then
    echo "ERRO: não achei o Python do Wine em $PYTHON" >&2
    echo "Confira se o prefixo $WINEPREFIX está certo." >&2
    exit 1
fi

AGENTE_EXE="../dist/BlueControl_Metricas.exe"
REMOTE_EXE="../dist/BlueControl_Remote.exe"
if [ ! -f "$REMOTE_EXE" ]; then
    echo "ERRO: não achei $REMOTE_EXE — rode ../compilar_remote.sh primeiro." >&2
    exit 1
fi
if [ ! -f "$AGENTE_EXE" ]; then
    echo "ERRO: não achei $AGENTE_EXE — rode ../compilar_metricas.sh primeiro." >&2
    exit 1
fi

export WINEPREFIX

echo "[1/2] Limpando build anterior..."
rm -rf build dist "ConfiguradorAgente.spec"

echo "[2/2] Compilando com PyInstaller..."
wine "$PYTHON" -m PyInstaller \
    --onefile \
    --noconsole \
    --uac-admin \
    --icon "Bluepay.ico" \
    --add-data "index.html;." \
    --add-data "style.css;." \
    --add-data "script.js;." \
    --add-data "$AGENTE_EXE;." \
    --add-data "$REMOTE_EXE;." \
    --name "ConfiguradorAgente" \
    configurador_agente.py

echo
if [ -f "dist/ConfiguradorAgente.exe" ]; then
    echo "✓ Gerado: dist/ConfiguradorAgente.exe"
    ls -la dist/ConfiguradorAgente.exe
else
    echo "✗ Build terminou mas o .exe não apareceu em dist/ — confira o log acima." >&2
    exit 1
fi
