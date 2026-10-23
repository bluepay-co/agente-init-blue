#!/usr/bin/env bash
# Compila agente_metricas.py (o agente COMPLETO — WMI, pywin32, serviço de
# verdade) pra .exe standalone, via Wine.
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
rm -rf build/BlueControl_Metricas dist/BlueControl_Metricas.exe "BlueControl_Metricas.spec"

echo "[2/2] Compilando com PyInstaller..."
wine "$PYTHON" -m PyInstaller \
    --onefile \
    --noconsole \
    --hidden-import=wmi \
    --hidden-import=pythoncom \
    --hidden-import=pywintypes \
    --hidden-import=win32timezone \
    --name "BlueControl_Metricas" \
    agente_metricas.py

echo
if [ -f "dist/BlueControl_Metricas.exe" ]; then
    echo "✓ Gerado: dist/BlueControl_Metricas.exe"
    ls -la dist/BlueControl_Metricas.exe
else
    echo "✗ Build terminou mas o .exe não apareceu em dist/ — confira o log acima." >&2
    exit 1
fi
