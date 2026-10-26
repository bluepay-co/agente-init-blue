"""Instalação da VPN (OpenVPN) nesta máquina.

O instalador é o gerado pelo OpenVPN Client Export do pfSense — um SFX 7-Zip
que roda dois passos:

    msiexec.exe /i openvpn-install.msi /norestart   instala o cliente
    openvpn-postinstall.exe                         grava o perfil da empresa

Ou seja, o perfil (.ovpn) vem junto: não é preciso distribuir nada à parte.
O que ele NÃO traz é a credencial, porque cada colaborador tem a sua — e é
isso que este módulo acrescenta, gravando um arquivo de credencial e
apontando o perfil para ele com `auth-user-pass`.

Segue o mesmo contrato de usuarios.py/programas.py/monitoramento.py: não
importa agente.py; recebe os helpers por init().
"""

import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

# ── Injetado por init() ───────────────────────────────────────────────────
_broadcast = lambda evento: None
_registrar = lambda msg: None
_resolver = lambda rel: None
_NO_WINDOW = 0


def init(broadcast, registrar, resolver, no_window):
    global _broadcast, _registrar, _resolver, _NO_WINDOW
    _broadcast, _registrar = broadcast, registrar
    _resolver, _NO_WINDOW = resolver, no_window


# Canal próprio, como o mon_* da aba Monitoramento: o handler de cada aba
# ignora os eventos das outras, então um progresso de VPN não pode chegar
# como se fosse da instalação do agente.
def _log(msg: str, tipo: str = "muted"):
    _broadcast({"type": "vpn_log", "msg": msg, "tipo": tipo})
    _registrar(f"[vpn] {msg}")


def _progresso(pct: int, texto: str, ok: bool = False, erro: bool = False):
    _broadcast({"type": "vpn_progresso", "pct": pct, "texto": texto,
                "ok": ok, "erro": erro})


# ── Caminhos ──────────────────────────────────────────────────────────────
# O MSI de 64 bits instala em Program Files; mantemos o (x86) por garantia de
# máquina antiga com cliente de 32 bits já instalado.
_DIRS_OPENVPN = (
    Path(r"C:\Program Files\OpenVPN"),
    Path(r"C:\Program Files (x86)\OpenVPN"),
)
# O instalador do pfSense é versionado com o nome do servidor no arquivo, que
# muda de export para export — por isso o glob, e não um nome fixo.
_PADROES_INSTALADOR = ("installers/openvpn-*.exe", "openvpn-*.exe")
_ARQ_CREDENCIAL = "bluecontrol-auth.txt"


def _dir_openvpn() -> Optional[Path]:
    for d in _DIRS_OPENVPN:
        if (d / "config").is_dir() or (d / "bin").is_dir():
            return d
    return None


def localizar_instalador() -> Optional[Path]:
    """Acha o instalador ao lado do AgenteBlue.exe ou embutido nele."""
    for padrao in _PADROES_INSTALADOR:
        pasta, _, mascara = padrao.rpartition("/")
        base = _resolver(pasta) if pasta else _resolver(".")
        if not base:
            continue
        achados = sorted(Path(base).glob(mascara))
        if achados:
            return achados[0]
    return None


def _dirs_perfil() -> List[Path]:
    """Todos os lugares onde um .ovpn pode estar.

    Não basta <OpenVPN>\\config: o pfSense grava ali, mas o OpenVPN 2.6+ usa
    config-auto para o que sobe com o serviço e %USERPROFILE%\\OpenVPN\\config
    para o perfil do usuário — e era essa última que faltava, deixando a aba
    dizer "instalado, sem perfil" numa máquina que tinha o perfil.
    """
    dirs = []
    d = _dir_openvpn()
    if d:
        dirs += [d / "config", d / "config-auto"]
    perfil_usuario = os.environ.get("USERPROFILE")
    if perfil_usuario:
        dirs.append(Path(perfil_usuario) / "OpenVPN" / "config")
    dados = os.environ.get("ProgramData")
    if dados:
        dirs.append(Path(dados) / "OpenVPN" / "config")
    return [p for p in dirs if p.is_dir()]


# Diretivas do .ovpn que apontam para OUTRO arquivo. O export do pfSense usa
# caminho relativo ("ca saturno-...-ca.crt"), resolvido contra a pasta do
# perfil -- um .ovpn sozinho numa pasta nao conecta, e o openvpn morre na
# largada sem nunca falar com o servidor. Isso nao e erro de credencial nem
# de rede, e antes caia no mesmo "indeterminado" que uma porta bloqueada.
_DIRETIVAS_COM_ARQUIVO = ("ca", "cert", "key", "tls-auth", "tls-crypt",
                          "tls-crypt-v2", "dh", "crl-verify", "pkcs12")


def refs_arquivo(perfil: Path) -> List[str]:
    """Os arquivos que o .ovpn referencia, como escritos nele."""
    try:
        texto = perfil.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    refs = []
    for linha in texto.splitlines():
        partes = linha.strip().split()
        if not partes or partes[0] not in _DIRETIVAS_COM_ARQUIVO or len(partes) < 2:
            continue
        ref = partes[1].strip('"')
        # Chave inline (<ca>...</ca>) nao referencia arquivo nenhum.
        if ref.startswith("["):
            continue
        refs.append(ref)
    return refs


def arquivos_faltando(perfil: Path) -> List[str]:
    """Arquivos que o .ovpn referencia e que nao estao ao lado dele."""
    faltando = []
    for ref in refs_arquivo(perfil):
        alvo = Path(ref)
        if not alvo.is_absolute():
            alvo = perfil.parent / ref
        if not alvo.exists():
            faltando.append(ref)
    return faltando


def mover_para_autostart(perfil: Path) -> Tuple[Path, str]:
    """Move o perfil (e os arquivos que ele referencia) para config-auto.

    E config-auto que o OpenVPNService sobe no boot, como SYSTEM e sem
    ninguem logado. Em config, o perfil so conecta quando alguem abre o GUI e
    clica -- que e justamente o trabalho manual que esta aba existe para tirar.

    MOVE em vez de copiar: o mesmo perfil nas duas pastas aparece duas vezes
    no GUI e convida a uma segunda conexao em paralelo com a do servico.
    """
    d = _dir_openvpn()
    if not d:
        return perfil, "pasta do OpenVPN não encontrada"
    destino = d / "config-auto"
    if perfil.parent == destino:
        return perfil, ""
    try:
        destino.mkdir(parents=True, exist_ok=True)
        # Os companheiros primeiro: um perfil em config-auto sem o CA ao lado
        # nao sobe, e o servico nao tem onde reclamar.
        for ref in refs_arquivo(perfil):
            origem = Path(ref)
            if origem.is_absolute():
                continue
            origem = perfil.parent / ref
            if origem.exists() and not (destino / ref).exists():
                shutil.copy2(str(origem), str(destino / ref))
        alvo = destino / perfil.name
        shutil.move(str(perfil), str(alvo))
        return alvo, ""
    except Exception as e:
        return perfil, f"não consegui mover o perfil para config-auto: {e}"


def ativar_servico() -> Tuple[bool, str]:
    """Deixa o OpenVPNService subindo no boot e o inicia agora.

    O instalador deixa este servico em DEMAND_START: sem isto o perfil fica em
    config-auto e nada o sobe, que e o mesmo resultado de nao ter mexido.
    """
    try:
        subprocess.run(["sc", "config", "OpenVPNService", "start=", "auto"],
                       capture_output=True, text=True, errors="ignore",
                       timeout=30, creationflags=_NO_WINDOW)
        # Reinicia: se ja estava rodando, ele so le config-auto na partida.
        subprocess.run(["sc", "stop", "OpenVPNService"],
                       capture_output=True, text=True, errors="ignore",
                       timeout=60, creationflags=_NO_WINDOW)
        r = subprocess.run(["sc", "start", "OpenVPNService"],
                           capture_output=True, text=True, errors="ignore",
                           timeout=60, creationflags=_NO_WINDOW)
        saida = (r.stdout or "") + (r.stderr or "")
        # 1056 = ja esta rodando; e sucesso para o que queremos.
        if r.returncode == 0 or "1056" in saida:
            return True, ""
        return False, saida.strip()[:200] or f"sc start devolveu {r.returncode}"
    except Exception as e:
        return False, str(e)


def escolher_perfil(perfis: List[Path]) -> Optional[Path]:
    """O perfil que de fato tem chance de conectar.

    Pegar perfis[0] as cegas era um erro: _dirs_perfil() varre quatro pastas,
    e um .ovpn orfao em qualquer uma delas virava o perfil testado, falhando
    sempre -- com a credencial certa e o servidor no ar.
    """
    for perfil in perfis:
        if not arquivos_faltando(perfil):
            return perfil
    return perfis[0] if perfis else None


# Nome que aparece no OpenVPN GUI. O export do pfSense nomeia o arquivo com
# servidor+protocolo+porta ("saturno-UDP4-1194-config.ovpn"), que e o que o
# colaborador ve na bandeja. Trocar o nome do ARQUIVO .ovpn basta: as
# diretivas ca/tls-auth apontam para os outros arquivos pelos nomes deles,
# que nao mudam.
NOME_AMIGAVEL = "BluePay VPN"


def renomear_perfil(perfil: Path, nome: str = NOME_AMIGAVEL) -> Tuple[Path, str]:
    """Renomeia o .ovpn para o nome que o usuario ve. Devolve (perfil, aviso).

    Nunca sobrescreve: se o destino existe, fica com o nome atual. Falhar aqui
    e cosmetico -- a VPN funciona com qualquer nome -- entao o erro vira aviso
    e a instalacao segue.
    """
    alvo = perfil.with_name(f"{nome}.ovpn")
    if alvo == perfil:
        return perfil, ""
    if alvo.exists():
        return perfil, ""
    try:
        perfil.rename(alvo)
        return alvo, ""
    except Exception as e:
        return perfil, f"não consegui renomear {perfil.name}: {e}"


def _perfis() -> List[Path]:
    """Perfis .ovpn instalados, em qualquer um dos diretórios conhecidos.

    Recursivo (rglob) porque algumas versões criam uma subpasta por perfil.
    """
    achados = []
    vistos = set()
    for pasta in _dirs_perfil():
        for arq in sorted(pasta.rglob("*.ovpn")):
            if arq not in vistos:
                vistos.add(arq)
                achados.append(arq)
    return achados


def status() -> dict:
    """Estado da VPN nesta máquina, para a aba decidir o que mostrar."""
    d = _dir_openvpn()
    perfis = _perfis()
    # Credencial gravada é por perfil: basta uma para o técnico saber que a
    # máquina já foi configurada.
    com_credencial = []
    for perfil in perfis:
        try:
            if _linha_auth(perfil.read_text(encoding="utf-8", errors="ignore")):
                com_credencial.append(perfil.name)
        except Exception:
            pass

    instalador = localizar_instalador()
    return {
        "instalado":      bool(d),
        "pasta":          str(d) if d else "",
        "perfis":         [p.name for p in perfis],
        "com_credencial": com_credencial,
        "instalador":     instalador.name if instalador else "",
        "servico":        _estado_servico(),
        # Conexao automatica = perfil em config-auto E servico ativo.
        # Uma coisa sem a outra da o mesmo resultado de nada feito.
        "autoconexao":    any(p.parent.name == "config-auto"
                              for p in perfis),
    }


def _estado_servico() -> str:
    """OpenVPNService sobe os perfis de config-auto no boot."""
    try:
        r = subprocess.run(["sc", "query", "OpenVPNService"], capture_output=True,
                           text=True, creationflags=_NO_WINDOW)
        if "RUNNING" in r.stdout:
            return "rodando"
        if "STOPPED" in r.stdout:
            return "parado"
        if r.returncode != 0:
            return "nao_instalado"
    except Exception:
        pass
    return "desconhecido"


# ── Credencial ────────────────────────────────────────────────────────────

def _linha_auth(texto: str) -> Optional[str]:
    """Devolve a linha auth-user-pass COM arquivo, se houver.

    Um `auth-user-pass` pelado (sem caminho) significa "pergunte ao usuário" —
    é o que o pfSense exporta. Com caminho, lê do arquivo. Os dois casos
    precisam ser distinguidos, senão a troca de credencial não acontece e a
    conexão segue pedindo senha na tela.
    """
    for linha in texto.splitlines():
        limpa = linha.strip()
        # Split antes do startswith: "auth-user-pass-verify" é outra diretiva e
        # passaria por um startswith cru, fazendo o status mentir e o
        # _apontar_perfil reescrever a linha errada.
        partes = limpa.split(None, 1)
        if partes and partes[0] == "auth-user-pass" and len(partes) > 1:
            return limpa
    return None


def _validar(usuario: str, senha: str) -> str:
    if not usuario.strip():
        return "Usuário da VPN em branco."
    if not senha:
        return "Senha da VPN em branco."
    # O arquivo é lido linha a linha pelo OpenVPN: uma quebra de linha no meio
    # da senha partiria a credencial em duas e a conexão falharia com um erro
    # que não aponta para cá.
    if "\n" in usuario or "\r" in usuario or "\n" in senha or "\r" in senha:
        return "Usuário e senha não podem conter quebra de linha."
    return ""


def _gravar_credencial(perfil: Path, usuario: str, senha: str) -> Tuple[bool, str]:
    """Grava o arquivo de credencial ao lado do perfil e restringe o acesso.

    Formato do OpenVPN: usuário na primeira linha, senha na segunda. Fica em
    texto puro — é o que o `auth-user-pass <arquivo>` aceita —, então a ACL é
    a única proteção real e precisa ser aplicada antes de qualquer conteúdo
    sensível entrar no arquivo.
    """
    destino = perfil.parent / _ARQ_CREDENCIAL
    try:
        # Conteúdo primeiro, ACL depois. A ordem inversa ja quebrou aqui: o
        # icacls era chamado com os nomes "SYSTEM"/"Administrators", que em
        # Windows português são "SISTEMA"/"Administradores". O comando falhava
        # DEPOIS de aplicar /inheritance:r, o arquivo ficava sem nenhuma
        # permissão e a gravação seguinte morria com acesso negado.
        destino.write_text(f"{usuario}\n{senha}\n", encoding="utf-8")
    except Exception as e:
        return False, f"não consegui criar {destino}: {e}"

    # SIDs bem conhecidos no lugar dos nomes: *S-1-5-18 é o SYSTEM e
    # *S-1-5-32-544 o grupo Administradores em qualquer idioma do Windows.
    r = subprocess.run(
        ["icacls", str(destino), "/inheritance:r",
         "/grant:r", "*S-1-5-18:(F)", "/grant:r", "*S-1-5-32-544:(F)"],
        capture_output=True, text=True, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        # Não derruba a instalação — a VPN precisa funcionar —, mas o arquivo
        # fica com as permissões herdadas da pasta, e em Program Files isso
        # significa legível por qualquer usuário da máquina. Precisa aparecer.
        saida = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " ")
        _log(f"ATENÇÃO: não consegui restringir o acesso a {destino.name} "
             f"({saida or 'icacls falhou'}). A senha está gravada, mas legível "
             "por outros usuários desta máquina.", "warn")
    return True, str(destino)


def _caminho_ovpn(caminho: Path) -> str:
    """Caminho do Windows como o OpenVPN exige dentro de um .ovpn.

    O proprio openvpn trata "\\" como escape de shell ao ler o arquivo de
    configuracao e recusa o perfil inteiro:

        Options warning: Bad backslash ('\\') usage in ...ovpn:12: remember
        that backslashes are treated as shell-escapes ... you should use
        double backslashes such as "c:\\\\openvpn\\\\static.key"

    Ele sai na hora, antes de abrir socket -- nenhuma linha de rede no log, o
    que fazia a falha parecer servidor sem resposta. Barra normal e aceita no
    Windows e nao tem escape nenhum para dar errado, entao e o que gravamos.
    """
    return str(caminho).replace("\\", "/")


def _apontar_perfil(perfil: Path, credencial: Path) -> Tuple[bool, str]:
    """Troca o `auth-user-pass` do perfil para apontar ao arquivo.

    Reescreve a linha existente em vez de acrescentar outra: duas diretivas
    auth-user-pass fazem o OpenVPN usar a última e avisar no log, o que
    funcionaria por acidente e quebraria na próxima reinstalação.
    """
    try:
        texto = perfil.read_text(encoding="utf-8", errors="ignore")
        nova = f'auth-user-pass "{_caminho_ovpn(credencial)}"'
        # \s ou fim de linha depois do nome: sem isso o padrão casa também com
        # auth-user-pass-verify e trocaria a diretiva errada.
        alvo = r"^[ \t]*auth-user-pass(?=[ \t]|$).*$"
        if re.search(alvo, texto, re.M):
            # Substituição por FUNÇÃO, não por string: re.sub interpreta
            # escapes no texto de troca, e o caminho do Windows traz
            # "C:\Program Files", onde \P não é escape válido e estoura
            # "bad escape \p at position 18". Uma função devolve o literal.
            texto = re.sub(alvo, lambda _m: nova, texto, count=1, flags=re.M)
        else:
            texto = texto.rstrip() + "\n" + nova + "\n"
        perfil.write_text(texto, encoding="utf-8")
        return True, ""
    except Exception as e:
        return False, str(e)


# ── Verificação da credencial ─────────────────────────────────────────────

# O que o openvpn.exe imprime quando a credencial e recusada pelo servidor.
_MARCAS_AUTH_FALHOU = ("AUTH_FAILED", "auth-failure", "authentication failed")
_MARCA_SUCESSO = "Initialization Sequence Completed"


# Causas de rede que o openvpn relata, em portugues claro. Sem isto a aba
# devolvia uma linha crua do log e o tecnico nao tinha o que fazer com ela:
# "nao conectou" nao diz se o servidor esta fora, se a porta esta fechada ou
# se o nome nem resolveu -- e sao acoes completamente diferentes.
_CAUSAS_REDE = (
    ("RESOLVE: Cannot resolve host",
     "o nome do servidor da VPN nao resolveu em DNS"),
    ("Could not determine IPv4/IPv6 protocol",
     "o nome do servidor da VPN nao resolveu em DNS"),
    ("Connection refused",
     "o servidor respondeu recusando a porta (servico da VPN parado?)"),
    ("No Route to Host",
     "sem rota ate o servidor da VPN"),
    ("Network is unreachable",
     "sem rota ate o servidor da VPN"),
    ("TLS Error: TLS key negotiation failed",
     "nada respondeu na porta da VPN dentro do tempo "
     "(porta bloqueada no caminho, ou -- se esta maquina esta DENTRO da rede "
     "do proprio servidor -- falta NAT reflection/hairpin no firewall)"),
    ("TLS handshake failed",
     "nada respondeu na porta da VPN dentro do tempo "
     "(porta bloqueada no caminho, ou -- se esta maquina esta DENTRO da rede "
     "do proprio servidor -- falta NAT reflection/hairpin no firewall)"),
    ("Cannot open file",
     "o perfil referencia um arquivo (CA, chave tls-auth) que nao esta na "
     "pasta dele"),
    ("Cannot pre-load keyfile",
     "o perfil referencia um arquivo (CA, chave tls-auth) que nao esta na "
     "pasta dele"),
    ("No such file or directory",
     "o perfil referencia um arquivo (CA, chave tls-auth) que nao esta na "
     "pasta dele"),
    ("Bad backslash",
     "o perfil tem um caminho do Windows com barra invertida simples, que o "
     "openvpn trata como escape e recusa"),
    ("Options error",
     "o perfil tem uma diretiva que o openvpn recusou"),
    ("Inactivity timeout",
     "nenhuma resposta do servidor da VPN"),
    ("All connections have been connect-retry-max",
     "nenhuma resposta do servidor da VPN"),
)


def _mesma_rede(destino: str) -> bool:
    """Se o host do "remote" esta na mesma /24 desta maquina.

    Serve para NAO repetir o aviso de NAT reflection quando ele nao se aplica:
    num teste via 4G o aviso so desvia a investigacao para o firewall.
    """
    host = (destino or "").split()[0] if (destino or "").strip() else ""
    if not host:
        return False
    try:
        ip_remoto = socket.gethostbyname(host)
    except Exception:
        return False
    try:
        # connect() em UDP nao envia pacote: so faz o SO escolher a interface
        # de saida, que e o jeito portatil de descobrir o IP local em uso.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            s.connect((ip_remoto, 1194))
            ip_local = s.getsockname()[0]
    except Exception:
        return False
    return ip_local.rsplit(".", 1)[0] == ip_remoto.rsplit(".", 1)[0]


def causa_rede(linhas: List[str]) -> str:
    """Traduz o log do openvpn.exe na causa de rede, se houver uma conhecida."""
    for linha in linhas:
        for marca, causa in _CAUSAS_REDE:
            if marca.lower() in (linha or "").lower():
                return causa
    return ""


def destino_perfil(perfil: Path) -> str:
    """O "remote host porta [proto]" do .ovpn, para dizer o que foi tentado.

    Sem isto o log diz que falhou mas nao contra o que: se o perfil aponta
    para um nome publico e a maquina esta na LAN do proprio firewall, a falha
    e esperada e nao tem nada a ver com a credencial.
    """
    try:
        texto = perfil.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    for linha in texto.splitlines():
        partes = linha.strip().split()
        if partes and partes[0] == "remote" and len(partes) > 1:
            return " ".join(partes[1:])
    return ""


def classificar_linha(linha: str) -> str:
    """Classifica uma linha do openvpn.exe: "auth_falhou", "ok" ou "" (segue).

    Separado de testar_credencial para poder ser testado sem OpenVPN instalado
    — é esta lógica que decide se o técnico vai ser mandado redigitar a senha.
    """
    baixa = (linha or "").lower()
    if any(m.lower() in baixa for m in _MARCAS_AUTH_FALHOU):
        return "auth_falhou"
    if _MARCA_SUCESSO in (linha or ""):
        return "ok"
    return ""


def testar_credencial(perfil: Path,
                      segundos: int = 75) -> Tuple[str, str, List[str]]:
    """Conecta de verdade para saber se a credencial funciona.

    Sem isto, uma senha errada passava como instalação bem-sucedida e só
    aparecia dias depois, quando o colaborador tentasse usar a VPN — e aí sem
    nada na tela ligando o problema à senha digitada aqui.

    Devolve ("ok" | "auth_falhou" | "indeterminado", detalhe, linhas_do_log).
    "indeterminado"
    é honesto: servidor fora do ar ou porta bloqueada não dizem nada sobre a
    senha, e tratar isso como erro de credencial mandaria o técnico redigitar
    uma senha que está certa.
    """
    d = _dir_openvpn()
    exe = (d / "bin" / "openvpn.exe") if d else None
    if not exe or not exe.exists():
        return "indeterminado", "openvpn.exe não encontrado para testar", []

    try:
        proc = subprocess.Popen(
            [str(exe), "--config", perfil.name, "--verb", "3",
             "--connect-retry-max", "1"],
            cwd=str(perfil.parent), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="ignore",
            creationflags=_NO_WINDOW)
    except Exception as e:
        return "indeterminado", f"não consegui executar o openvpn.exe: {e}", []

    resultado, detalhe = ["indeterminado", "o teste não concluiu a tempo"]
    linhas: List[str] = []

    def ler():
        nonlocal resultado, detalhe
        for linha in proc.stdout:
            linhas.append(linha.rstrip())
            veredito = classificar_linha(linha)
            if veredito == "auth_falhou":
                resultado, detalhe = veredito, linha.strip()
                return
            if veredito == "ok":
                resultado, detalhe = veredito, "túnel estabelecido"
                return

    # Thread: o openvpn fica conectado quando dá certo, então esperar o
    # processo terminar travaria aqui para sempre.
    leitor = threading.Thread(target=ler, daemon=True)
    leitor.start()
    leitor.join(timeout=segundos)

    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    if resultado == "indeterminado" and linhas:
        # Causa conhecida primeiro; a última linha é o fallback honesto.
        detalhe = causa_rede(linhas) or linhas[-1][:200]
    return resultado, detalhe, linhas


def _salvar_log(linhas: List[str]) -> str:
    """Grava o log do teste em disco e devolve o caminho.

    As ultimas linhas na tela cabem numa olhada, mas nao bastam para
    investigar: a causa de um handshake que morre costuma estar no inicio do
    log, que ja rolou para fora.
    """
    if not linhas:
        return ""
    base = os.environ.get("ProgramData") or os.environ.get("TEMP") or "."
    try:
        pasta = Path(base) / "BlueControl"
        pasta.mkdir(parents=True, exist_ok=True)
        alvo = pasta / f"vpn-teste-{time.strftime('%Y%m%d-%H%M%S')}.log"
        alvo.write_text("\n".join(linhas), encoding="utf-8", errors="ignore")
        return str(alvo)
    except Exception:
        return ""


def _ativar_conexao_automatica(perfis: List[Path]) -> None:
    """Sobe o OpenVPNService, para o tunel subir sozinho no boot."""
    em_auto = [p for p in perfis if p.parent.name == "config-auto"]
    if not em_auto:
        _log("O perfil não está em config-auto — a conexão vai continuar "
             "manual, pelo OpenVPN GUI.", "warn")
        return
    ok, erro = ativar_servico()
    if ok:
        _log("OpenVPNService ativo e em inicialização automática: a VPN "
             "conecta sozinha no boot, sem ninguém logar nem clicar.", "ok")
    else:
        _log(f"Não consegui ativar o OpenVPNService: {erro}", "warn")
        _log("O perfil já está em config-auto — ative o serviço à mão com "
             "\"sc config OpenVPNService start= auto\" e reinicie.", "info")


# ── Instalação ────────────────────────────────────────────────────────────

def instalar(params: dict) -> None:
    """Roda em thread separada: publica o andamento por SSE e não retorna nada."""
    try:
        usuario = (params.get("usuario") or "").strip()
        senha = params.get("senha") or ""
        erro = _validar(usuario, senha)
        if erro:
            _log(erro, "err")
            _progresso(100, "Dados incompletos", erro=True)
            return

        if not _dir_openvpn():
            instalador = localizar_instalador()
            if not instalador:
                _log("Instalador da VPN não encontrado — coloque o "
                     "openvpn-*.exe na pasta installers\\ ao lado do "
                     "AgenteBlue.exe.", "err")
                _progresso(100, "Instalador não encontrado", erro=True)
                return

            _progresso(20, "Instalando o OpenVPN…")
            _log(f"Executando {instalador.name}", "muted")
            _log("A janela do instalador do OpenVPN vai abrir — conclua nela "
                 "(Next/Install) e esta aba continua sozinha depois.", "info")
            # SEM creationflags nem capture_output, de propósito. Este SFX abre
            # a UI do MSI (o RunProgram embutido não usa /qn), e com
            # CREATE_NO_WINDOW a janela nascia invisível: ninguém clicava, o
            # processo ficava esperando até o timeout, o MSI não concluía e o
            # openvpn-postinstall.exe -- que grava o perfil -- nunca rodava.
            # Era isso que produzia "carregando infinitamente", "não aparece na
            # lista de programas" e "sem perfil", tudo ao mesmo tempo.
            # capture_output também sai: num processo longo e falante, encher o
            # buffer do pipe trava os dois lados.
            try:
                r = subprocess.run([str(instalador)], timeout=1800)
                if r.returncode != 0:
                    _log(f"O instalador terminou com código {r.returncode}.", "warn")
            except subprocess.TimeoutExpired:
                _log("O instalador passou de 30 minutos — verifique se ficou "
                     "aguardando algum clique na tela.", "err")
                _progresso(100, "Instalação não concluiu", erro=True)
                return

            # O serviço e as pastas aparecem logo depois do MSI terminar, mas
            # não instantaneamente.
            for _ in range(10):
                if _dir_openvpn():
                    break
                time.sleep(2)
        else:
            _log("OpenVPN já estava instalado — só vou gravar a credencial.", "info")

        if not _dir_openvpn():
            _log("Instalei, mas não encontrei a pasta do OpenVPN. A máquina "
                 "pode precisar reiniciar antes de continuar.", "err")
            _progresso(100, "OpenVPN não localizado", erro=True)
            return
        _log(f"OpenVPN em {_dir_openvpn()}", "ok")

        _progresso(60, "Procurando o perfil da VPN…")
        perfis = _perfis()
        if not perfis:
            _log("Nenhum perfil .ovpn encontrado. O instalador do pfSense "
                 "costuma gravá-lo junto — confirme que você usou o export "
                 "completo, e não só o cliente.", "err")
            _progresso(100, "Perfil não encontrado", erro=True)
            return
        _log(f"Perfil encontrado: {', '.join(p.name for p in perfis)}", "ok")

        # Renomear antes de gravar a credencial: o caminho do perfil muda, e
        # apontar a credencial no arquivo antigo deixaria o novo sem ela.
        renomeados = []
        for perfil in perfis:
            novo_perfil, aviso = renomear_perfil(perfil)
            if aviso:
                _log(aviso, "warn")
            renomeados.append(novo_perfil)
        if renomeados != perfis:
            _log(f"Perfil renomeado para \"{NOME_AMIGAVEL}\" — é esse nome que "
                 "aparece no OpenVPN GUI.", "ok")
        perfis = renomeados

        # Para config-auto ANTES de gravar a credencial: o caminho absoluto da
        # credencial vai escrito dentro do perfil, e mover depois deixaria a
        # diretiva apontando para a pasta antiga.
        _progresso(70, "Ativando a conexão automática…")
        movidos = []
        for perfil in perfis:
            novo_perfil, aviso = mover_para_autostart(perfil)
            if aviso:
                _log(aviso, "warn")
                _log("A VPN vai funcionar, mas só quando alguém conectar pelo "
                     "OpenVPN GUI.", "warn")
            movidos.append(novo_perfil)
        perfis = movidos

        _progresso(80, "Gravando a credencial…")
        falhas = 0
        for perfil in perfis:
            ok, info = _gravar_credencial(perfil, usuario, senha)
            if not ok:
                _log(f"Não consegui gravar a credencial de {perfil.name}: {info}", "err")
                falhas += 1
                continue
            ok2, erro2 = _apontar_perfil(perfil, Path(info))
            if not ok2:
                _log(f"Credencial gravada mas não consegui apontar "
                     f"{perfil.name} para ela: {erro2}", "err")
                falhas += 1
                continue
            _log(f"{perfil.name} configurado para o usuário {usuario}", "ok")

        if falhas:
            _progresso(100, "VPN instalada, mas a credencial não foi gravada",
                       erro=True)
            return

        _registrar(f"[vpn] credencial gravada para o usuario {usuario} em "
                   f"{len(perfis)} perfil(is)")

        # Gravar não é o mesmo que funcionar. Conecta de verdade: senha errada
        # passaria como sucesso e só apareceria quando o colaborador precisasse
        # da VPN, sem nada ligando o problema ao que foi digitado aqui.
        _progresso(90, "Testando a conexão com a credencial…")
        _log("Conectando para conferir a credencial (até ~1 minuto)…", "muted")
        alvo = escolher_perfil(perfis) or perfis[0]
        faltando = arquivos_faltando(alvo)
        if faltando:
            # Nao da para testar credencial com um perfil incompleto: o
            # openvpn nem chega a falar com o servidor, e o resultado seria
            # indistinguivel de porta bloqueada.
            _log(f"O perfil {alvo.name} referencia arquivos que nao estao em "
                 f"{alvo.parent}: {', '.join(faltando)}", "err")
            _log("Reinstale a VPN pela aba (o instalador grava o perfil junto "
                 "com o CA e a chave tls-auth na mesma pasta).", "info")
            _progresso(100, "Perfil incompleto — credencial nao pode ser testada",
                       ok=True)
            return

        destino = destino_perfil(alvo)
        if destino:
            _log(f"O perfil {alvo.name} conecta em: {destino}", "muted")
        situacao, detalhe, log_vpn = testar_credencial(alvo)

        if situacao == "auth_falhou":
            _log(f"O servidor RECUSOU a credencial de {usuario}: {detalhe}", "err")
            _log("Confira usuário e senha e instale de novo — a aba sobrescreve "
                 "a credencial gravada.", "info")
            _progresso(100, "Credencial recusada pelo servidor da VPN", erro=True)
            return

        if situacao == "ok":
            _log(f"Credencial de {usuario} aceita — o túnel subiu e foi "
                 "encerrado.", "ok")
            # Só agora: subir o serviço com uma credencial recusada deixaria
            # ele tentando e falhando em loop a cada boot.
            _ativar_conexao_automatica(perfis)
            _progresso(100, "VPN pronta, conectando automaticamente!", ok=True)
            return

        # Indeterminado: não dá para afirmar que a senha está errada, e mandar
        # redigitar uma senha correta é pior do que avisar o que houve.
        _log(f"Não consegui confirmar a credencial: {detalhe}", "warn")
        _log("O servidor não recusou a credencial — ele não respondeu. Isso é "
             "rede, não a senha: o OpenVPN só chega a validar usuário e senha "
             "depois do handshake TLS, e aqui ele não passou dessa etapa.",
             "info")
        if destino and _mesma_rede(destino):
            # Condicional de proposito: este aviso aparecia sempre que havia
            # um destino, inclusive num teste via 4G, onde hairpin nao tem
            # nada a ver -- e mandava investigar o firewall por nada.
            _log(f"Esta máquina está na MESMA rede de {destino}: o firewall "
                 "precisa de NAT reflection (hairpin) para aceitar conexão "
                 "vinda de dentro no próprio endereço externo. Teste de uma "
                 "rede externa (4G) para confirmar a credencial.", "info")
        _log("A credencial está gravada e o perfil aponta para ela — não "
             "precisa reinstalar.", "ok")
        # As ultimas linhas do log cruas: sem elas, "nao conectou" nao tem como
        # ser investigado depois sem repetir o teste na maquina do cliente.
        uteis = [l for l in log_vpn if l.strip()]
        if not uteis:
            _log("O openvpn.exe não produziu nenhuma saída — ou não chegou a "
                 "rodar, ou foi encerrado antes de logar.", "warn")
        for linha in uteis[-12:]:
            _log(f"  openvpn: {linha[:180]}", "muted")
        destino_log = _salvar_log(uteis)
        if destino_log:
            _log(f"Log completo do teste salvo em {destino_log}", "info")
        # Ativa mesmo assim: "indeterminado" nao e credencial recusada, e
        # deixar a conexao manual por uma duvida de rede seria pior -- se a
        # credencial estiver certa, o tunel sobe no proximo boot.
        _ativar_conexao_automatica(perfis)
        _progresso(100, "VPN instalada — credencial não pôde ser testada",
                   ok=True)

    except Exception as e:
        _log(f"Erro inesperado: {e}", "err")
        _progresso(100, "Falha na instalação da VPN", erro=True)


# ══════════════════════════════════════════════
#  LIMPEZA (desfaz a instalação da VPN)
# ══════════════════════════════════════════════

def _codigos_produto() -> List[str]:
    """Códigos MSI do OpenVPN instalados, lidos do registro.

    Pelo código, e não pelo UninstallString: o UninstallString do MSI vem como
    "MsiExec.exe /I{GUID}" — /I, de instalar — e repetir isso abriria a tela
    de manutenção em vez de desinstalar.
    """
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$r = @('HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*');"
        "Get-ItemProperty $r | Where-Object { $_.DisplayName -like '*OpenVPN*' } |"
        "ForEach-Object { $_.PSChildName }"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW)
        achados = []
        for linha in (r.stdout or "").splitlines():
            codigo = linha.strip()
            # Só GUIDs: a chave de um instalador não-MSI tem nome livre e
            # passar isso ao msiexec só geraria erro.
            if re.fullmatch(r"\{[0-9A-Fa-f-]{36}\}", codigo):
                achados.append(codigo)
        return achados
    except Exception as e:
        _registrar(f"[vpn] falha ao listar produtos OpenVPN: {e}")
        return []


def limpar(params: dict) -> None:
    """Remove a VPN desta máquina: credenciais, perfis e o próprio OpenVPN.

    As credenciais saem PRIMEIRO. Se a desinstalação falhar no meio, o que não
    pode sobrar é a senha do colaborador em disco — o cliente sem credencial é
    só um incômodo, a credencial órfã é um problema.
    """
    try:
        _progresso(10, "Apagando credenciais gravadas…")
        apagados = 0
        for perfil in _perfis():
            cred = perfil.parent / _ARQ_CREDENCIAL
            try:
                if cred.exists():
                    cred.unlink()
                    apagados += 1
                    _log(f"Credencial {cred.name} apagada", "ok")
            except Exception as e:
                _log(f"Não consegui apagar {cred}: {e}", "err")
        if not apagados:
            _log("Nenhuma credencial gravada encontrada", "muted")

        _progresso(30, "Apagando perfis .ovpn…")
        for perfil in _perfis():
            try:
                perfil.unlink()
                _log(f"Perfil {perfil.name} apagado", "ok")
            except Exception as e:
                _log(f"Não consegui apagar {perfil.name}: {e}", "err")

        _progresso(50, "Desinstalando o OpenVPN…")
        codigos = _codigos_produto()
        if not codigos and _dir_openvpn():
            # Pasta sem registro = instalação que começou e não terminou (foi o
            # que a janela invisível do MSI provocava). Não há o que desinstalar;
            # o caminho é apagar o que sobrou e instalar de novo.
            _log("A pasta do OpenVPN existe mas ele não está registrado como "
                 "programa instalado — sinal de instalação interrompida no meio. "
                 "Vou apagar o que sobrou para você poder instalar do zero.", "warn")
        elif not codigos:
            _log("OpenVPN não aparece na lista de programas instalados "
                 "(pode já ter sido removido).", "info")
        for codigo in codigos:
            _log(f"Desinstalando {codigo}…", "muted")
            try:
                r = subprocess.run(
                    ["msiexec", "/x", codigo, "/qn", "/norestart"],
                    capture_output=True, timeout=900, creationflags=_NO_WINDOW)
                # 3010 = sucesso, mas pede reinício. Não é falha.
                if r.returncode in (0, 3010):
                    _log(f"{codigo} desinstalado"
                         + (" (requer reinício)" if r.returncode == 3010 else ""), "ok")
                else:
                    _log(f"msiexec devolveu {r.returncode} para {codigo}", "warn")
            except subprocess.TimeoutExpired:
                _log(f"A desinstalação de {codigo} passou de 15 minutos.", "err")

        _progresso(80, "Removendo o que sobrou…")
        for alvo in _dirs_perfil():
            for resto in list(alvo.rglob("*.ovpn")) + list(alvo.rglob(_ARQ_CREDENCIAL)):
                try:
                    resto.unlink()
                    _log(f"Removido {resto}", "ok")
                except Exception as e:
                    _log(f"Não consegui remover {resto}: {e}", "warn")

        # Sem registro no sistema não há desinstalador: a pasta órfã some aqui,
        # senão a próxima instalação encontra o diretório e acha que já existe
        # um OpenVPN válido.
        if not codigos:
            for d in _DIRS_OPENVPN:
                if not d.is_dir():
                    continue
                try:
                    shutil.rmtree(d)
                    _log(f"Pasta {d} removida", "ok")
                except Exception as e:
                    _log(f"Não consegui remover {d}: {e} — feche o OpenVPN GUI "
                         "se estiver aberto.", "warn")

        if _dir_openvpn():
            _log("A pasta do OpenVPN ainda existe — se a desinstalação pediu "
                 "reinício, reinicie e rode a limpeza de novo.", "warn")
            _progresso(100, "Limpeza parcial — veja o log", erro=True)
        else:
            _registrar("[vpn] VPN removida da maquina")
            _progresso(100, "VPN removida — pode instalar do zero", ok=True)

    except Exception as e:
        _log(f"Erro inesperado na limpeza: {e}", "err")
        _progresso(100, "Falha na limpeza da VPN", erro=True)
