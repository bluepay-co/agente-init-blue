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
import subprocess
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


def _perfis() -> List[Path]:
    """Perfis .ovpn instalados. O pfSense grava em <OpenVPN>\\config, mas
    versões recentes usam config-auto para o que sobe com o serviço."""
    d = _dir_openvpn()
    if not d:
        return []
    achados = []
    for sub in ("config", "config-auto"):
        p = d / sub
        if p.is_dir():
            achados.extend(sorted(p.glob("*.ovpn")))
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
        if limpa.startswith("auth-user-pass") and len(limpa.split(None, 1)) > 1:
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


def _apontar_perfil(perfil: Path, credencial: Path) -> Tuple[bool, str]:
    """Troca o `auth-user-pass` do perfil para apontar ao arquivo.

    Reescreve a linha existente em vez de acrescentar outra: duas diretivas
    auth-user-pass fazem o OpenVPN usar a última e avisar no log, o que
    funcionaria por acidente e quebraria na próxima reinstalação.
    """
    try:
        texto = perfil.read_text(encoding="utf-8", errors="ignore")
        nova = f'auth-user-pass "{credencial}"'
        if re.search(r"^\s*auth-user-pass.*$", texto, re.M):
            texto = re.sub(r"^\s*auth-user-pass.*$", nova, texto, count=1, flags=re.M)
        else:
            texto = texto.rstrip() + "\n" + nova + "\n"
        perfil.write_text(texto, encoding="utf-8")
        return True, ""
    except Exception as e:
        return False, str(e)


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
            # O SFX pode abrir a janela do MSI; é esperado e o técnico
            # acompanha. Sem timeout curto: instalar driver TAP demora.
            try:
                r = subprocess.run([str(instalador)], capture_output=True,
                                   timeout=900, creationflags=_NO_WINDOW)
                if r.returncode != 0:
                    _log(f"O instalador terminou com código {r.returncode}.", "warn")
            except subprocess.TimeoutExpired:
                _log("O instalador passou de 15 minutos — verifique se ficou "
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
            _progresso(100, "VPN instalada, mas a credencial falhou", erro=True)
            return

        _registrar(f"[vpn] credencial gravada para o usuario {usuario} em "
                   f"{len(perfis)} perfil(is)")
        _log("A VPN conecta sem pedir senha. Para subir junto com o Windows, "
             "deixe o perfil em config-auto e o serviço OpenVPNService em "
             "automático.", "info")
        _progresso(100, "VPN pronta!", ok=True)

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
        if not codigos:
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
        for d in _DIRS_OPENVPN:
            for sub in ("config", "config-auto"):
                alvo = d / sub
                if not alvo.is_dir():
                    continue
                for resto in list(alvo.glob("*.ovpn")) + list(alvo.glob(_ARQ_CREDENCIAL)):
                    try:
                        resto.unlink()
                        _log(f"Removido {resto}", "ok")
                    except Exception as e:
                        _log(f"Não consegui remover {resto}: {e}", "warn")

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
