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
    agente.vpn.init(agente._broadcast, agente._registrar,
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
                    "/vpn.js", "/assets/bluepay-ico.ico"):
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
                    "/api/usuarios/hostname", "/api/monitoramento/status",
                    "/api/vpn/status"):
        checar(f"GET  {caminho} sem token", status(caminho), 403)
    for caminho in ("/api/execute", "/api/usuarios/criar", "/api/usuarios/editar",
                    "/api/usuarios/excluir", "/api/programas/desinstalar",
                    "/api/usuarios/hostname",
                    "/api/monitoramento/buscar", "/api/monitoramento/cadastrar",
                    "/api/monitoramento/instalar", "/api/monitoramento/excluir",
                    "/api/monitoramento/limpar", "/api/vpn/instalar",
                    "/api/vpn/limpar"):
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

    print("\n[7] Nº de série lido da BIOS")
    # Fabricante que não preenche o campo grava um texto de preenchimento, e ele
    # chega como se fosse série. Cadastrado, daria dez máquinas com o mesmo
    # "To Be Filled By O.E.M." no BlueControl -- pior que vazio, porque parece
    # legítimo. Só o que sobra deste filtro preenche o campo sozinho.
    valido = agente.monitoramento._serial_valido
    for bruto in ("5CG812ABCD", "JH7KL32", "  PF2ABCDE  "):
        checar(f"aceita série real {bruto.strip()!r}", valido(bruto), bruto.strip())
    for lixo in ("To Be Filled By O.E.M.", "System Serial Number", "Default string",
                 "None", "", "   ", "0", "...", "AB"):
        checar(f"descarta {lixo!r}", valido(lixo), "")
    checar("/status expõe 'numero_serie'", "numero_serie" in corpo, True)

    print("\n[7b] AnyDesk ID lido da máquina")
    # O ID é numérico de 9-10 dígitos e o --get-id as vezes devolve agrupado
    # ("123 456 789"). Lixo aqui vira acesso remoto apontando pra máquina errada.
    val_ad = agente.monitoramento._anydesk_id_valido
    checar("aceita ID simples", val_ad("123456789"), "123456789")
    checar("aceita ID agrupado", val_ad("123 456 789"), "123456789")
    checar("aceita ID com quebra de linha", val_ad(" 987654321\n"), "987654321")
    for lixo in ("", "abc", "12", "0" * 20, "Display ID:"):
        checar(f"descarta {lixo[:14]!r}", val_ad(lixo), "")
    checar("/status expõe 'anydesk_id'", "anydesk_id" in corpo, True)

    print("\n[6b] Todo getElementById aponta para um id que existe")
    # Um id errado aqui não quebra na hora: getElementById devolve null e o
    # TypeError só estoura quando o técnico clica, dentro de um try/catch que
    # mostra uma mensagem sobre outra coisa. Já aconteceu com o botão de
    # limpeza (monUrlApi/monToken em vez de monCfgUrlApi/monCfgToken).
    import glob as _glob
    import re as _re
    _html = open(os.path.join(RAIZ, "web", "index.html"), encoding="utf-8").read()
    _ids = set(_re.findall(r'id="([^"]+)"', _html))
    _quebrados = []
    for _js in sorted(_glob.glob(os.path.join(RAIZ, "web", "*.js"))):
        _src = open(_js, encoding="utf-8").read()
        for _m in _re.finditer(r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)", _src):
            if _m.group(1) not in _ids:
                _quebrados.append(f"{os.path.basename(_js)}:{_src[:_m.start()].count(chr(10)) + 1}"
                                  f" -> {_m.group(1)}")
    checar(f"referências quebradas ({len(_ids)} ids no HTML)", _quebrados, [])

    print("\n[6c] Toda view do showView está na regra flex do CSS")
    # showView() aplica display:flex. Sem flex-direction:column os cards se
    # alinham em LINHA e a aba vira um amasso horizontal. Ja aconteceu duas
    # vezes: com a #viewMonitor e com a #viewVpn.
    _css = open(os.path.join(RAIZ, "web", "style.css"), encoding="utf-8").read()
    _js_views = open(os.path.join(RAIZ, "web", "usuarios.js"), encoding="utf-8").read()
    _bloco = _re.search(r"const views = \{(.*?)\};", _js_views, _re.S)
    _nomes = _re.findall(r"'(view[A-Za-z]+)'", _bloco.group(1)) if _bloco else []
    _regra = _re.search(r"^([^{]*#viewMain[^{]*)\{([^}]*)\}", _css, _re.M)
    _seletores = _regra.group(1) if _regra else ""
    _coluna = "column" in (_regra.group(2) if _regra else "")
    _fora = [v for v in _nomes if f"#{v}" not in _seletores]
    checar(f"views registradas no showView ({len(_nomes)})", _fora, [])
    checar("a regra aplica flex-direction:column", _coluna, True)

    print("\n[7d] VPN")
    # Usuario e senha vao para um arquivo que o OpenVPN le linha a linha: uma
    # quebra de linha no meio partiria a credencial em duas e a conexao
    # falharia com um erro que nao aponta para ca.
    _val = agente.vpn._validar
    checar("credencial valida passa", _val("joao.silva", "senha123"), "")
    checar("usuario vazio e recusado", bool(_val("", "x")), True)
    checar("senha vazia e recusada", bool(_val("joao", "")), True)
    checar("quebra de linha no usuario", bool(_val("jo\nao", "x")), True)
    checar("quebra de linha na senha", bool(_val("joao", "se\nnha")), True)
    # Um auth-user-pass pelado significa "pergunte na tela" -- so com caminho
    # a credencial gravada e usada de fato.
    _la = agente.vpn._linha_auth
    checar("auth-user-pass sem arquivo nao conta", _la("client\nauth-user-pass\n"), None)
    checar("auth-user-pass com arquivo conta",
           bool(_la('client\nauth-user-pass "C:\\x\\a.txt"\n')), True)
    # So GUID vira argumento do msiexec: nome livre de instalador nao-MSI
    # geraria erro, e um valor inesperado ali e comando para o desinstalador.
    _guid = _re.fullmatch(r"\{[0-9A-Fa-f-]{36}\}", "{12345678-1234-1234-1234-123456789ABC}")
    checar("GUID valido e aceito pelo filtro", bool(_guid), True)
    checar("nome livre nao passa",
           bool(_re.fullmatch(r"\{[0-9A-Fa-f-]{36}\}", "OpenVPNClient")), False)
    _st = agente.vpn.status()
    for campo in ("instalado", "perfis", "com_credencial", "instalador", "servico"):
        checar(f"/status da VPN devolve '{campo}'", campo in _st, True)

    print("\n[7c] Exclusão exige patrimônio e número de série")
    # A API só apaga a máquina cuja série bate com a lida da BIOS. Se o agente
    # mandasse a requisição sem série, a API recusaria com 400 e o técnico veria
    # um erro do servidor para algo que dava para barrar aqui.
    excluir = agente.monitoramento.excluir
    checar("sem patrimônio nem sai da máquina",
           excluir("http://127.0.0.1:9", "tok", "")[0], 0)
    checar("sem patrimônio explica o motivo",
           "branco" in excluir("http://127.0.0.1:9", "tok", "")[1]["detail"], True)
    checar("sem token nem sai da máquina",
           excluir("http://127.0.0.1:9", "", "0042")[0], 0)
    # Fora do Windows não há série para ler, então a exclusão é barrada antes
    # de qualquer requisição — exatamente o que deve acontecer numa máquina
    # cuja BIOS não devolve série.
    codigo, corpo_exc = excluir("http://127.0.0.1:9", "tok", "0042")
    checar("sem série lida, não tenta excluir", codigo, 0)
    checar("sem série, explica por quê",
           "número de série" in corpo_exc["detail"], True)

    print("\n[8] URL da API malformada")
    # A URL vem de um campo que o técnico edita. Antes, uma URL vazia estourava
    # ValueError na montagem do urllib.request.Request -- exceção que não é
    # HTTPError nem URLError, então subia pelo handler e o navegador recebia a
    # conexão fechada, sem mensagem nenhuma na tela.
    normalizar = agente.monitoramento.normalizar_url
    checar("sem esquema ganha http://",
           normalizar("192.168.10.11:8550")[0], "http://192.168.10.11:8550")
    checar("barra final é removida",
           normalizar("http://192.168.10.11:8550/")[0], "http://192.168.10.11:8550")
    checar("vazia é recusada com motivo", bool(normalizar("")[1]), True)
    checar("esquema não-HTTP é recusado", bool(normalizar("ftp://x")[1]), True)

    for rota, corpo in (("buscar", {"url_api": "", "token_agente": "x", "q": "a"}),
                        ("cadastrar", {"url_api": "ftp://x", "token_agente": "x"})):
        pedido = urllib.request.Request(
            f"{base}/api/monitoramento/{rota}", method="POST",
            data=json.dumps(corpo).encode(),
            headers=dict(autenticado, **{"Content-Type": "application/json"}))
        try:
            with urllib.request.urlopen(pedido, timeout=5) as r:
                codigo, lido = r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            codigo, lido = exc.code, json.loads(exc.read() or b"{}")
        checar(f"/{rota} responde em vez de derrubar", codigo, 502)
        checar(f"/{rota} explica o motivo", bool(lido.get("detail")), True)

    servidor.shutdown()
    print("\n" + ("RESULTADO: tudo certo" if not _falhas else f"RESULTADO: {_falhas} falha(s)"))
    return 1 if _falhas else 0


if __name__ == "__main__":
    sys.exit(main())
