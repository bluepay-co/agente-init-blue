#!/bin/bash
# ============================================================
# BlueControl IT — Diagnóstico do agente Mac
# Abra com DUPLO CLIQUE (ou: bash + arrastar no Terminal).
# Vai pedir a senha do Mac (sudo) e gerar 'diag_mac.txt' NESTA pasta.
# Copie o diag_mac.txt para o pendrive e mande para a TI.
# ============================================================
cd "$(dirname "$0")" || exit 1
OUT="$PWD/diag_mac.txt"

echo "Gerando diagnóstico... (pode pedir a senha do Mac)"

{
  echo "===== DIAGNÓSTICO AGENTE MAC — $(date) ====="
  echo
  echo "== 1) Serviço carregado? =="
  sudo launchctl list | grep -i bluecontrol || echo "(nenhum serviço bluecontrol carregado)"
  echo
  echo "== 2) plist do serviço =="
  ls -l /Library/LaunchDaemons/com.bluecontrol.metricas.plist 2>/dev/null || echo "(plist não existe)"
  echo
  echo "== 3) Arquivos em /usr/local/bluecontrol =="
  sudo ls -l /usr/local/bluecontrol 2>/dev/null || echo "(pasta não existe)"
  echo
  echo "== 4) config.json (sem o token) =="
  sudo /usr/bin/python3 -c "import json;d=json.load(open('/usr/local/bluecontrol/config.json'));d['agent_secret']='***';print(json.dumps(d,indent=2,ensure_ascii=False))" 2>/dev/null || echo "(não consegui ler o config)"
  echo
  echo "== 5) metricas.log (fim) =="
  sudo tail -50 /usr/local/bluecontrol/metricas.log 2>/dev/null || echo "(sem metricas.log)"
  echo
  echo "== 6) metricas_err.log (fim) =="
  sudo tail -40 /usr/local/bluecontrol/metricas_err.log 2>/dev/null || echo "(sem err log)"
  echo
  echo "== 7) Alcança o servidor? =="
  URL=$(sudo /usr/bin/python3 -c "import json;print(json.load(open('/usr/local/bluecontrol/config.json'))['api_urls'][0])" 2>/dev/null)
  echo "URL configurada: $URL"
  curl -s -m 8 -o /dev/null -w "resposta HTTP: %{http_code} (em %{time_total}s)\n" "$URL/" || echo "(curl falhou — sem rota/servidor)"
  echo
  echo "== 8) Rodando o agente na mão por 12s (mostra erros de verdade) =="
  sudo /usr/local/bluecontrol/BlueControl_Metricas_Mac & B=$!
  sleep 12
  sudo kill "$B" 2>/dev/null
  echo
  echo "== 9) metricas.log depois do teste =="
  sudo tail -20 /usr/local/bluecontrol/metricas.log 2>/dev/null || echo "(sem metricas.log)"
  echo
  echo "===== FIM ====="
} > "$OUT" 2>&1

echo
echo "PRONTO! Diagnóstico salvo em:"
echo "   $OUT"
echo "Copie o 'diag_mac.txt' para o pendrive e mande para a TI."
open . 2>/dev/null || true
read -r -p "Pressione Enter para sair..." _ 2>/dev/null || true
