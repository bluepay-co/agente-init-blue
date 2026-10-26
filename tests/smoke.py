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

    print("\n[6d] Nenhum módulo usado sem import")
    # "import shutil" esquecido nao quebra o carregamento: so estoura com
    # NameError na hora em que a funcao roda -- e aqui isso seria no meio de
    # uma limpeza de VPN, o pior momento possivel. Ja aconteceu em vpn.py.
    import builtins as _bi
    _faltando = []
    for _f in sorted(_glob.glob(os.path.join(RAIZ, "src", "*.py"))):
        _arv = __import__("ast").parse(open(_f, encoding="utf-8").read())
        _ast = __import__("ast")
        _conhecidos = set(dir(_bi))
        for _no in _ast.walk(_arv):
            if isinstance(_no, _ast.Import):
                for _a in _no.names:
                    _conhecidos.add((_a.asname or _a.name).split(".")[0])
            elif isinstance(_no, _ast.ImportFrom):
                for _a in _no.names:
                    _conhecidos.add(_a.asname or _a.name)
            elif isinstance(_no, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                _conhecidos.add(_no.name)
            elif isinstance(_no, _ast.Name) and isinstance(_no.ctx, _ast.Store):
                _conhecidos.add(_no.id)
            elif isinstance(_no, _ast.arg):
                _conhecidos.add(_no.arg)
            elif isinstance(_no, _ast.ExceptHandler) and _no.name:
                _conhecidos.add(_no.name)
        for _no in _ast.walk(_arv):
            if isinstance(_no, _ast.Attribute) and isinstance(_no.value, _ast.Name):
                if _no.value.id not in _conhecidos:
                    _faltando.append(f"{os.path.basename(_f)}:{_no.value.id}")
    checar("modulos usados sem import", sorted(set(_faltando)), [])

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
    # Classificacao das linhas do openvpn.exe: e ela que decide se o tecnico
    # vai ser mandado redigitar uma senha. Um falso "auth_falhou" por queda de
    # rede manda trocar uma senha que esta certa.
    _cl = agente.vpn.classificar_linha
    checar("AUTH_FAILED e recusa",
           _cl("Mon Oct 26 ... AUTH: Received control message: AUTH_FAILED"), "auth_falhou")
    checar("authentication failed e recusa",
           _cl("SENT CONTROL: authentication failed"), "auth_falhou")
    checar("tunel completo e sucesso",
           _cl("Initialization Sequence Completed"), "ok")
    for _neutra in ("TLS: Initial packet from 10.0.0.1",
                    "Connection refused (WSAECONNREFUSED)",
                    "TLS Error: TLS handshake failed",
                    "OpenVPN 2.7.4 starting"):
        checar(f"segue adiante: {_neutra[:28]!r}", _cl(_neutra), "")
    # auth-user-pass-verify e OUTRA diretiva: casar com ela faria o status
    # mentir e a reescrita trocar a linha errada.
    checar("auth-user-pass-verify nao conta",
           agente.vpn._linha_auth("client\nauth-user-pass-verify x via-env\n"), None)
    # Apontar o perfil para a credencial passa por re.sub, que interpreta
    # escapes no texto de troca. O caminho real e "C:\\Program Files\\OpenVPN\\
    # config\\...", onde \\P nao e escape valido: isso estourava "bad escape \\p
    # at position 18" e deixava a credencial gravada mas nunca usada.
    import tempfile as _tf
    from pathlib import Path as _Path
    _perfil = _Path(_tf.mkdtemp()) / "saturno-UDP4-1194-config.ovpn"
    _perfil.write_text("client\ndev tun\nauth-user-pass\nremote vpn 1194\n",
                       encoding="utf-8")
    _cred = _Path(r"C:\Program Files\OpenVPN\config\bluecontrol-auth.txt")
    _ok, _erro = agente.vpn._apontar_perfil(_perfil, _cred)
    checar("aponta perfil com caminho que tem \\P", (_ok, _erro), (True, ""))
    _texto = _perfil.read_text(encoding="utf-8")
    # Literal quanto ao conteudo, com a barra convertida: o escape do re.sub
    # era um bug, e a barra invertida crua era outro -- os dois na mesma linha.
    checar("caminho sai no perfil, com barra normal",
           agente.vpn._caminho_ovpn(_cred) in _texto, True)
    checar("perfil apontado e reconhecido depois",
           bool(agente.vpn._linha_auth(_texto)), True)
    checar("nao duplicou a diretiva", _texto.count("auth-user-pass"), 1)

    # Falha de rede nao e falha de senha: o openvpn so valida a credencial
    # DEPOIS do handshake TLS. Classificar errado manda o tecnico redigitar
    # uma senha correta, que foi o que aconteceu no teste dentro da LAN.
    _cr = agente.vpn.causa_rede
    checar("handshake TLS vazio aponta porta/hairpin",
           "hairpin" in _cr(["TLS Error: TLS key negotiation failed to occur"]), True)
    checar("connection refused tem causa propria",
           "recusando a porta" in _cr(["Connection refused (WSAECONNREFUSED)"]), True)
    checar("DNS que nao resolve e identificado",
           "DNS" in _cr(["RESOLVE: Cannot resolve host address: vpn.x"]), True)
    checar("log sem causa conhecida nao inventa",
           _cr(["OpenVPN 2.7.4 starting", "TLS: Initial packet"]), "")
    checar("AUTH_FAILED nao vira causa de rede", _cr(["AUTH_FAILED"]), "")
    # O destino e o que distingue "senha errada" de "estou na rede do proprio
    # firewall": sem ele o log diz que falhou, mas nao contra o que.
    _pd = _Path(_tf.mkdtemp()) / "p.ovpn"
    _pd.write_text("client\nremote vpn.exemplo.com.br 1194 udp4\nauth-user-pass\n",
                   encoding="utf-8")
    checar("le o remote do perfil",
           agente.vpn.destino_perfil(_pd), "vpn.exemplo.com.br 1194 udp4")
    _pd.write_text("client\nremote-random\nauth-user-pass\n", encoding="utf-8")
    checar("remote-random nao e remote", agente.vpn.destino_perfil(_pd), "")
    checar("perfil inexistente nao estoura",
           agente.vpn.destino_perfil(_Path("/nao/existe.ovpn")), "")

    # Perfil incompleto: o export do pfSense aponta "ca" e "tls-auth" por
    # caminho RELATIVO, resolvido contra a pasta do perfil. Um .ovpn orfao
    # (em config-auto, no %USERPROFILE%...) morre antes de falar com o
    # servidor, e isso era indistinguivel de porta bloqueada.
    _pasta = _Path(_tf.mkdtemp())
    _real = ("dev tun\nclient\nremote 187.102.159.62 1194 udp4\n"
             "auth-user-pass\nca saturno-ca.crt\ntls-auth saturno-tls.key 1\n")
    _orfao = _pasta / "orfao.ovpn"
    _orfao.write_text(_real, encoding="utf-8")
    checar("perfil sem CA nem chave e detectado",
           sorted(agente.vpn.arquivos_faltando(_orfao)),
           ["saturno-ca.crt", "saturno-tls.key"])
    _completo_dir = _Path(_tf.mkdtemp())
    _completo = _completo_dir / "bom.ovpn"
    _completo.write_text(_real, encoding="utf-8")
    (_completo_dir / "saturno-ca.crt").write_text("x", encoding="utf-8")
    (_completo_dir / "saturno-tls.key").write_text("x", encoding="utf-8")
    checar("perfil completo nao acusa falta",
           agente.vpn.arquivos_faltando(_completo), [])
    # A escolha do perfil era perfis[0] as cegas -- o orfao ganhava e a
    # instalacao falhava com a credencial certa e o servidor no ar.
    checar("escolhe o perfil completo, nao o primeiro",
           agente.vpn.escolher_perfil([_orfao, _completo]), _completo)
    checar("sem perfil completo, devolve o primeiro",
           agente.vpn.escolher_perfil([_orfao]), _orfao)
    checar("sem perfil nenhum, devolve None",
           agente.vpn.escolher_perfil([]), None)
    # Chave inline nao referencia arquivo: acusar falta ali bloquearia um
    # perfil perfeitamente valido.
    _inline = _pasta / "inline.ovpn"
    _inline.write_text("client\n<ca>\n-----BEGIN-----\n</ca>\n", encoding="utf-8")
    checar("chave inline nao conta como arquivo faltando",
           agente.vpn.arquivos_faltando(_inline), [])
    checar("arquivo que nao abre tem causa propria",
           "nao esta na pasta" in _cr(["Cannot open file 'x.crt'"]), True)

    # O openvpn trata "\\" como escape de shell DENTRO do .ovpn e recusa o
    # perfil inteiro ("Bad backslash usage ...:12"), saindo antes de abrir
    # socket -- nenhuma linha de rede no log, o que fazia a falha parecer
    # servidor sem resposta. Verificado contra o servidor real: com barra
    # normal o handshake completa e a credencial e lida do arquivo.
    _co = agente.vpn._caminho_ovpn
    checar("barra invertida nao sobrevive na diretiva",
           "\\" in _co(_Path(r"C:\Program Files\OpenVPN\config\a.txt")), False)
    checar("caminho sai com barra normal",
           _co(_Path(r"C:\Program Files\OpenVPN\config\a.txt")),
           "C:/Program Files/OpenVPN/config/a.txt")
    _pb = _Path(_tf.mkdtemp()) / "b.ovpn"
    _pb.write_text("client\nauth-user-pass\n", encoding="utf-8")
    agente.vpn._apontar_perfil(_pb, _Path(r"C:\Program Files\OpenVPN\cred.txt"))
    _lb = _pb.read_text(encoding="utf-8")
    checar("perfil gravado nao tem barra invertida", "\\" in _lb, False)
    checar("perfil gravado ainda e reconhecido",
           bool(agente.vpn._linha_auth(_lb)), True)
    checar("Bad backslash tem causa propria",
           "barra invertida" in _cr(["Options warning: Bad backslash usage in x.ovpn:12"]),
           True)

    # O nome do ARQUIVO .ovpn e o que o colaborador ve no OpenVPN GUI. O
    # export do pfSense gera "saturno-UDP4-1194-config.ovpn"; renomear o
    # arquivo basta, porque ca/tls-auth apontam para os outros arquivos pelos
    # nomes deles. Renomear DEPOIS de gravar a credencial deixaria o perfil
    # novo sem ela, entao a ordem importa.
    _rd = _Path(_tf.mkdtemp())
    _orig = _rd / "saturno-UDP4-1194-config.ovpn"
    _orig.write_text("client\nca saturno-ca.crt\n", encoding="utf-8")
    _novo, _av = agente.vpn.renomear_perfil(_orig)
    checar("renomeia para o nome amigavel",
           _novo.name, agente.vpn.NOME_AMIGAVEL + ".ovpn")
    checar("renomear nao avisa erro", _av, "")
    checar("o arquivo antigo sai do caminho", _orig.exists(), False)
    checar("conteudo preservado", "ca saturno-ca.crt" in _novo.read_text(), True)
    # Idempotente: reinstalar nao pode falhar nem duplicar.
    _novo2, _av2 = agente.vpn.renomear_perfil(_novo)
    checar("renomear de novo e inofensivo", (_novo2, _av2), (_novo, ""))
    # Nunca sobrescreve um perfil que ja existe com esse nome.
    _outro = _rd / "outro.ovpn"
    _outro.write_text("client\n", encoding="utf-8")
    _r3, _av3 = agente.vpn.renomear_perfil(_outro)
    checar("nao sobrescreve perfil existente", _r3, _outro)
    checar("perfil existente segue intacto", _novo.exists(), True)

    # Conexao automatica: e config-auto que o OpenVPNService sobe no boot,
    # como SYSTEM e sem ninguem logado. Em config o perfil so conecta quando
    # alguem abre o GUI e clica -- o trabalho manual que a aba existe para
    # tirar. Mover tem que levar os companheiros: um perfil sem o CA ao lado
    # nao sobe, e o servico nao tem onde reclamar.
    _ad = _Path(_tf.mkdtemp())
    _ap = _ad / "p.ovpn"
    _ap.write_text('client\nca c.crt\ntls-auth t.key 1\nauth-user-pass "/x/a.txt"\n',
                   encoding="utf-8")
    (_ad / "c.crt").write_text("ca", encoding="utf-8")
    (_ad / "t.key").write_text("key", encoding="utf-8")
    checar("le as referencias do perfil",
           sorted(agente.vpn.refs_arquivo(_ap)), ["c.crt", "t.key"])
    # auth-user-pass NAO e arquivo de chave: entrar nessa lista faria o perfil
    # ser copiado com a credencial e, pior, acusar falta dela.
    checar("credencial nao entra nas referencias",
           "/x/a.txt" in agente.vpn.refs_arquivo(_ap), False)
    checar("perfil completo nao acusa falta (apos refatorar)",
           agente.vpn.arquivos_faltando(_ap), [])
    (_ad / "c.crt").unlink()
    checar("CA removido volta a ser acusado",
           agente.vpn.arquivos_faltando(_ap), ["c.crt"])

    _st = agente.vpn.status()
    for campo in ("instalado", "perfis", "com_credencial", "instalador",
                  "servico", "autoconexao"):
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
