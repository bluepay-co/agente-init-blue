#!/usr/bin/env bash
# Compila BlueControl_Remote.py (agente de acesso remoto — captura de tela
# H.264 + mouse/teclado) pra .exe standalone, via Wine.
#
# Dependências no Python do Wine (uma vez só):
#   pip install --only-binary=:all: av mss websockets pillow numpy==1.26.4
#   pip install pyautogui
# numpy fica no 1.26: o 2.x chama ucrtbase.crealf, que o Wine 9 não implementa,
# e o build do PyInstaller aborta ao importar.
set -euo pipefail
cd "$(dirname "$0")"

WINEPREFIX="$HOME/.wine64-agente"
PYTHON="$WINEPREFIX/drive_c/users/$USER/AppData/Local/Programs/Python/Python311/python.exe"

if [ ! -f "$PYTHON" ]; then
    echo "ERRO: não achei o Python do Wine em $PYTHON" >&2
    exit 1
fi

export WINEPREFIX

# Não apaga dist/ inteiro — o BlueControl_Metricas.exe mora na mesma pasta.
echo "[1/2] Limpando build anterior..."
rm -rf build/BlueControl_Remote dist/BlueControl_Remote.exe BlueControl_Remote.spec

echo "[2/2] Compilando com PyInstaller..."
wine "$PYTHON" -m PyInstaller \
    --onefile \
    --noconsole \
    --collect-all av \
    --name "BlueControl_Remote" \
    BlueControl_Remote.py

echo
if [ -f "dist/BlueControl_Remote.exe" ]; then
    echo "✓ Gerado: dist/BlueControl_Remote.exe"
    ls -la dist/BlueControl_Remote.exe
else
    echo "✗ Build terminou mas o .exe não apareceu em dist/ — confira o log acima." >&2
    exit 1
fi
