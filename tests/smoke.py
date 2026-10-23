"""Smoke test do servidor local do Agente Blue.

Verifica o que é fácil quebrar sem perceber ao mexer na estrutura ou nas rotas:
  • a interface e os assets continuam sendo servidos;
  • config.json e os instaladores NÃO são alcançáveis por HTTP;
  • toda rota /api/ exige o token da sessão;
  • o SSE abre com o token na query e entrega eventos.

Não executa nenhuma etapa do setup — só sobe o servidor HTTP. Por isso roda
em qualquer sistema, inclusive Linux:

    python tests/smoke.py
"""

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# O agente grava o log em %ProgramData%; aponta para um temporário descartável.
os.environ["ProgramData"] = tempfile.mkdtemp(prefix="agenteblue-smoke-")
sys.path.insert(0, os.path.join(RAIZ, "src"))

import agente  # noqa: E402

_falhas = 0


def checar(descricao: str, obtido, esperado) -> None:
    global _falhas
    ok = obtido == esperado
    _falhas += not ok
    print(f"  {'OK   ' if ok else 'FALHA'}  {descricao:44} -> {obtido} (esperado {esperado})")


def main() -> int:
    agente.usuarios.init(agente._log, agente._logged_user, agente._NO_WINDOW,
                         agente._gravar_script_protegido, agente._apagar_script)
    agente.programas.init(agente._log, agente._broadcast, agente._logged_user, agente._NO_WINDOW)
    agente.monitoramento.init(agente._broadcast, agente._registrar,
                              agente._resolver, agente._NO_WINDOW)

    porta = agente._find_free_port()
    servidor = agente._ThreadedServer(("127.0.0.1", porta), agente.AgenteHandler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    base = f"http://127.0.0.1:{porta}"
    token = agente._API_TOKEN

    def status(caminho, metodo="GET", cabecalhos=None, query_token=None):
        url = base + caminho + (f"?t={query_token}" if query_token else "")
        req = urllib.request.Request(
            url, method=metodo, data=b"{}" if metodo == "POST" else None)
        if metodo == "POST":
            req.add_header("Content-Type", "application/json")
        for chave, valor in (cabecalhos or {}).items():
            req.add_header(chave, valor)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    autenticado = {"X-Agente-Token": token}

    print("\n[1] Interface e assets")
    for caminho in ("/index.html", "/style.css", "/api.js", "/script.js",
                    "/usuarios.js", "/programas.js", "/monitoramento.js",
                    "/assets/bluepay-ico.ico"):
        checar(caminho, status(caminho), 200)

    print("\n[2] Arquivos sensíveis não podem vazar por HTTP")
    for caminho in ("/config.json", "/config.example.json",
                    "/installers/Anydesk.exe", "/src/agente.py"):
        checar(caminho, status(caminho), 404)

    print("\n[3] Rotas /api/ exigem o token da sessão")
    checar("GET  /api/version com token", status("/api/version", cabecalhos=autenticado), 200)
    checar("GET  /api/version sem token", status("/api/version"), 403)
    checar("GET  /api/version token errado",
           status("/api/version", cabecalhos={"X-Agente-Token": "errado"}), 403)
    checar("GET  token na query (modo SSE)",
           status("/api/version", query_token=token), 200)
    for caminho in ("/api/usuarios/listar", "/api/programas/listar",
                    "/api/monitoramento/status"):
        checar(f"GET  {caminho} sem token", status(caminho), 403)
    for caminho in ("/api/execute", "/api/usuarios/criar", "/api/usuarios/editar",
                    "/api/usuarios/excluir", "/api/programas/desinstalar",
                    "/api/monitoramento/buscar", "/api/monitoramento/cadastrar",
                    "/api/monitoramento/instalar"):
        checar(f"POST {caminho} sem token", status(caminho, "POST"), 403)

    print("\n[4] Origem e Host precisam ser da própria janela")
    checar("Origin externa, mesmo com token",
           status("/api/version", cabecalhos=dict(autenticado, Origin="http://evil.example")), 403)
    checar("Host externo (DNS rebinding)",
           status("/api/version", cabecalhos=dict(autenticado, Host="attacker.test")), 403)

    print("\n[5] Stream de eventos (SSE)")
    checar("SSE sem token", status("/api/stream"), 403)
    resposta = urllib.request.urlopen(f"{base}/api/stream?t={token}", timeout=10)
    checar("SSE com token", resposta.status, 200)
    threading.Timer(0.3, lambda: agente._broadcast({"tipo": "log", "msg": "ping"})).start()
    linha = resposta.readline().decode("utf-8")
    checar("evento entregue ao cliente", '"msg": "ping"' in linha, True)
    resposta.close()

    print("\n[6] Aba Monitoramento")
    # A interface preenche URL/token a partir daqui, inclusive em máquina nova
    # (existe=False) — se o /status parar de responder os campos, o cadastro
    # volta a falhar como já falhou uma vez.
    corpo = json.loads(urllib.request.urlopen(
        urllib.request.Request(f"{base}/api/monitoramento/status",
                               headers=autenticado), timeout=5).read())
    for campo in ("existe", "url_api", "token_agente", "intervalo_segundos",
                  "servico", "agentes_disponiveis"):
        checar(f"/status devolve '{campo}'", campo in corpo, True)
    checar("/status traz a URL padrão sem config", bool(corpo["url_api"]), True)
    # Sem token padrão no código: em máquina nova o técnico digita o dele.
    checar("/status NÃO inventa um token", corpo["token_agente"], "")

    # O canal mon_* é separado do da aba Setup: o handleEvent do script.js
    # ignora tipo desconhecido, e o da aba Monitoramento ignora 'log'/'etapa_*'.
    resposta = urllib.request.urlopen(f"{base}/api/stream?t={token}", timeout=10)
    threading.Timer(0.3, lambda: agente.monitoramento._progresso(42, "meio")).start()
    linha = resposta.readline().decode("utf-8")
    checar("progresso sai no canal mon_*", '"mon_progresso"' in linha, True)
    resposta.close()

    servidor.shutdown()
    print("\n" + ("RESULTADO: tudo certo" if not _falhas else f"RESULTADO: {_falhas} falha(s)"))
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
