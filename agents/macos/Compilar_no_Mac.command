#!/bin/bash
# ============================================================
# BlueControl IT — Compilar o instalador do macOS (1 clique)
# ============================================================
# Abra com DUPLO CLIQUE (ou arraste este arquivo para o Terminal
# e aperte Enter). Ele prepara um ambiente isolado, instala o
# que falta e gera o .app sozinho.
#
# Usa um ambiente virtual (venv) próprio para NÃO depender dos
# pacotes do Python do sistema — que costumam estar instalados
# como root e quebram o PyInstaller ("Permission denied").
# ============================================================

cd "$(dirname "$0")" || exit 1

# Grava TODO o log (tela + erros) num arquivo, pra facilitar o diagnóstico.
# Se der erro, é só copiar o "Compilar_LOG.txt" para o pendrive e mandar pra TI.
LOG_FILE="$PWD/Compilar_LOG.txt"
exec > >(tee "$LOG_FILE") 2>&1

echo "======================================================"
echo " BlueControl IT — Compilando o instalador do macOS"
echo "======================================================"
echo "(o log completo está sendo salvo em: $LOG_FILE)"
echo

pausa_no_fim() {
    echo
    echo "Pode fechar esta janela."
    read -r -p "Pressione Enter para sair..." _ 2>/dev/null || true
}

if [ "$(uname)" != "Darwin" ]; then
    echo "ERRO: rode este arquivo NUM MAC (o app da Apple não compila em outro sistema)."
    pausa_no_fim; exit 1
fi

# 1. Python 3
if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 não encontrado."
    echo "Instale as Ferramentas de Linha de Comando (vai abrir uma janela da Apple):"
    xcode-select --install 2>/dev/null || true
    echo
    echo "Depois que terminar de instalar, abra este arquivo de novo."
    pausa_no_fim; exit 1
fi
echo "[1/4] Python 3 encontrado: $(python3 --version 2>&1)"

# 2. Ambiente isolado (venv) — descarta um anterior pra começar limpo
VENV=".bcbuild"
echo "[2/4] Preparando ambiente isolado..."
rm -rf "$VENV"
if ! python3 -m venv "$VENV" >/tmp/bc_venv.log 2>&1; then
    echo "ERRO ao criar o ambiente isolado. Veja /tmp/bc_venv.log"
    tail -n 15 /tmp/bc_venv.log
    pausa_no_fim; exit 1
fi
VPY="$PWD/$VENV/bin/python"

# 3. Dependências DENTRO do venv (nada de sudo, nada do sistema)
echo "[3/4] Instalando dependências no ambiente isolado (pode demorar na 1a vez)..."
"$VPY" -m pip install --upgrade pip >/tmp/bc_pip.log 2>&1 || true
if ! "$VPY" -m pip install pyinstaller pywebview pyobjc psutil requests >>/tmp/bc_pip.log 2>&1; then
    echo "ERRO ao instalar dependências. Últimas linhas do log (/tmp/bc_pip.log):"
    tail -n 20 /tmp/bc_pip.log
    pausa_no_fim; exit 1
fi
echo "     Dependências prontas."

# 4. Build usando o Python do venv
echo "[4/4] Compilando (aguarde)..."
chmod +x compilar_mac.sh 2>/dev/null || true
if PYTHON="$VPY" bash compilar_mac.sh && [ -d "dist/BlueControl_Agente_Mac.app" ]; then
    # Deixa só o aplicativo: tira o .app das pastas de rascunho e apaga o resto
    rm -rf "BlueControl_Agente_Mac.app"
    mv "dist/BlueControl_Agente_Mac.app" "BlueControl_Agente_Mac.app"
    rm -rf build dist "$VENV" *.spec
    echo
    echo "======================================================"
    echo " PRONTO! O instalador está aqui:"
    echo "   $(pwd)/BlueControl_Agente_Mac.app"
    echo "======================================================"
    echo "As pastas de rascunho (build/dist) foram removidas — ficou só o app."
    echo "Copie o BlueControl_Agente_Mac.app para o Mac do usuário. Na 1a vez,"
    echo "abra com botão direito -> Abrir. Depois preencha o patrimônio e instale."
    open . 2>/dev/null || true
else
    echo
    echo "Algo falhou na compilação."
    echo "COPIE o arquivo 'Compilar_LOG.txt' (nesta mesma pasta) para o pendrive"
    echo "e mande para a TI — o erro completo está registrado lá."
    open . 2>/dev/null || true
fi
pausa_no_fim
