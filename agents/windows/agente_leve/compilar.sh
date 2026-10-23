#!/usr/bin/env bash
# Compila o Agente Leve (agente_monitoramento_leve.py) pra .exe standalone —
# a máquina de destino não precisa ter Python instalado.
# Mesmo Wine já usado pro Agente Blue e pro Configurador.
#
# Uso: ./compilar.sh
# Saída: dist/BlueControl_Lite.exe

set -euo pipefail
cd "$(dirname "$0")"

WINEPREFIX="$HOME/.wine64-agente"
PYTHON="$WINEPREFIX/drive_c/users/$USER/AppData/Local/Programs/Python/Python311/python.exe"

if [ ! -f "$PYTHON" ]; then
    echo "ERRO: não achei o Python do Wine em $PYTHON" >&2
    exit 1
fi

export WINEPREFIX

echo "[1/2] Limpando build anterior..."
rm -rf build dist "BlueControl_Lite.spec"

echo "[2/2] Compilando com PyInstaller..."
wine "$PYTHON" -m PyInstaller \
    --onefile \
    --noconsole \
    --name "BlueControl_Lite" \
    agente_monitoramento_leve.py

echo
if [ -f "dist/BlueControl_Lite.exe" ]; then
    echo "✓ Gerado: dist/BlueControl_Lite.exe"
    ls -la dist/BlueControl_Lite.exe
else
    echo "✗ Build terminou mas o .exe não apareceu em dist/ — confira o log acima." >&2
    exit 1
fi
