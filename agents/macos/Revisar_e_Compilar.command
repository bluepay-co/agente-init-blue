#!/bin/bash
# ============================================================
# BlueControl IT — Revisar e Compilar (macOS), tudo em 1
# Abra com DUPLO CLIQUE, ou no Terminal: bash + arrastar este arquivo.
# Ele REVISA os arquivos e, se tudo passar, PREPARA o ambiente e COMPILA.
# Log completo em: Revisao_LOG.txt
# ============================================================
cd "$(dirname "$0")" || exit 1
exec > >(tee "Revisao_LOG.txt") 2>&1

echo "=========================================================="
echo " BlueControl IT — Revisão e Compilação do instalador macOS"
echo "=========================================================="
echo

falhas=0
chk(){ if eval "$2" >/dev/null 2>&1; then echo "[OK]    $1"; else echo "[FALHA] $1"; falhas=$((falhas+1)); fi; }

echo "── 1) Ambiente ──────────────────────────────────────────"
chk "Está rodando no macOS"              '[ "$(uname)" = "Darwin" ]'
chk "python3 disponível"                 'command -v python3'

echo "── 2) Arquivos necessários ──────────────────────────────"
for f in instalar_bluecontrol_mac.py agente_metricas_mac.py compilar_mac.sh; do
  chk "existe: $f"                       "[ -f '$f' ]"
done

echo "── 3) Sintaxe Python ────────────────────────────────────"
chk "sintaxe instalar_bluecontrol_mac.py" 'python3 -m py_compile instalar_bluecontrol_mac.py'
chk "sintaxe agente_metricas_mac.py"      'python3 -m py_compile agente_metricas_mac.py'

echo "── 4) Conteúdo esperado (versão nova) ───────────────────"
chk "interface tem 'Cadastrar nova'"      'grep -q "Cadastrar nova" instalar_bluecontrol_mac.py'
chk "interface tem 'Já cadastrada'"       'grep -q "Já cadastrada" instalar_bluecontrol_mac.py'
chk "config grava tipo_os macos"          'grep -q "\"tipo_os\": \"macos\"" instalar_bluecontrol_mac.py'
chk "build embute webview"                'grep -q "collect-all webview" compilar_mac.sh'
chk "build usa caminho absoluto do agente" 'grep -qF "\$PWD/dist/BlueControl_Metricas_Mac" compilar_mac.sh'

echo
if [ "$falhas" -gt 0 ]; then
  echo ">>> REVISÃO FALHOU ($falhas item(ns)). Não vou compilar."
  echo ">>> Copie 'Revisao_LOG.txt' para o pendrive e mande para a TI."
  read -r -p "Pressione Enter para sair..." _ 2>/dev/null || true
  exit 1
fi
echo ">>> Revisão OK. Iniciando a compilação..."
echo

# ── Ambiente isolado (não usa os pacotes do Python do sistema) ──
VENV=".bcbuild"
echo "[build 1/3] Preparando ambiente isolado..."
rm -rf "$VENV"
if ! python3 -m venv "$VENV"; then
  echo ">>> ERRO ao criar o ambiente isolado."; read -r -p "Enter para sair..." _ 2>/dev/null || true; exit 1
fi
VPY="$PWD/$VENV/bin/python"

echo "[build 2/3] Instalando dependências (demora na 1a vez)..."
"$VPY" -m pip install --upgrade pip >/dev/null 2>&1 || true
if ! "$VPY" -m pip install pyinstaller pywebview pyobjc psutil requests; then
  echo ">>> ERRO ao instalar dependências (veja acima)."; read -r -p "Enter para sair..." _ 2>/dev/null || true; exit 1
fi

echo "[build 3/3] Compilando..."
chmod +x compilar_mac.sh 2>/dev/null || true
if PYTHON="$VPY" bash compilar_mac.sh && [ -d "dist/BlueControl_Agente_Mac.app" ]; then
  rm -rf "BlueControl_Agente_Mac.app"
  mv "dist/BlueControl_Agente_Mac.app" "BlueControl_Agente_Mac.app"
  rm -rf build dist "$VENV" *.spec
  echo
  echo "=========================================================="
  echo " ✔ PRONTO! Instalador aqui: $(pwd)/BlueControl_Agente_Mac.app"
  echo "=========================================================="
  echo "Abra com botão direito -> Abrir. Deve mostrar 'Já cadastrada' / 'Cadastrar nova'."
  open . 2>/dev/null || true
else
  echo
  echo ">>> A compilação falhou. Copie 'Revisao_LOG.txt' para o pendrive e mande para a TI."
fi
read -r -p "Pressione Enter para sair..." _ 2>/dev/null || true
