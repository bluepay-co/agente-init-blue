"""
monitoramento.py — Agente Blue (v3.0)
=====================================
Aba "Monitoramento": cadastra a máquina no BlueControl IT e instala o agente
de métricas + acesso remoto, sem editar C:\\BlueControl\\config.json na mão.

Veio do ConfiguradorAgente.exe, que era um programa separado. O configurador
repetia a infraestrutura inteira do agente (servidor HTTP, SSE, elevação a
Administrador, abertura de janela), então ao trazer o código para cá sobrou só
a lógica de domínio: falar com a API, gravar a config e registrar o serviço.

O que a instalação faz, em ordem:
  1. Resolve o patrimônio — busca uma máquina já cadastrada no BlueControl ou
     cadastra uma nova (endpoints /api/agente/equipamentos/*, autenticados só
     por X-Agent-Token: não precisa de login de usuário).
  2. Grava C:\\BlueControl\\config.json com o patrimônio resolvido.
  3. Copia BlueControl_Metricas.exe e registra o serviço Windows com
     "sc create". O .exe implementa o protocolo de serviço de verdade
     (StartServiceCtrlDispatcher via pywin32), então não precisa de NSSM.
  4. Copia BlueControl_Remote.exe como TAREFA AGENDADA no logon — serviço não
     serve aqui, ver _instalar_acesso_remoto.
  5. Habilita o WinRM e libera ping no firewall — sem isso, Bloquear/Reiniciar
     pelo painel não funciona nessa máquina.

Eventos SSE: publica em "mon_log" e "mon_progresso", canal próprio, para que o
log desta aba não se misture com o da aba Setup (que escuta "log" e "etapa_*").

Como o usuarios.py e o programas.py, NÃO importa o agente.py: recebe os
helpers via init().
"""

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

# ── Helpers injetados pelo agente (ver init) ──────────────────────────────────
_broadcast = lambda ev: None
_registrar = lambda msg: None
_resolver = lambda rel: None
_NO_WINDOW = 0

INSTALL_DIR  = Path(r"C:\BlueControl")
CONFIG_PATH  = INSTALL_DIR / "config.json"

AGENTE_EXE   = "BlueControl_Metricas.exe"
SERVICE_NAME = "BlueControlMetricas"
SERVICE_DISP = "BlueControl IT - Agente de Métricas"

REMOTE_EXE   = "BlueControl_Remote.exe"
REMOTE_TASK  = "BlueControlRemote"

WINRM_PORTA  = "5985"
# Agente leve antigo (instalar_agente_lite.bat). Se ficar de pé, a máquina
# manda métricas por dois agentes ao mesmo tempo.
LEGADO_SERVICE = "BlueControlLite"

DEFAULT_URL_API   = "http://192.168.10.11:8550"
DEFAULT_INTERVALO = 300

# Onde procurar os .exe dos agentes, na ordem. O _resolver() do agente olha a
# pasta ao lado do executável antes da cópia embutida, então "agents/..." cobre
# o build com pasta ao lado e o build monolítico; o último é o layout do
# repositório, para rodar direto do fonte.
_ORIGENS_EXE = ("agents/{nome}", "{nome}", "agents/windows/dist/{nome}")


def init(broadcast, registrar, resolver, no_window):
    """Chamado uma vez pelo agente para injetar os helpers compartilhados.

    Recebe `registrar` (grava no log em disco) em vez do `_log` do agente:
    quem publica na interface é este módulo, no canal da sua própria aba.
    """
    global _broadcast, _registrar, _resolver, _NO_WINDOW
    _broadcast = broadcast
    _registrar = registrar
    _resolver = resolver
    _NO_WINDOW = no_window


# ══════════════════════════════════════════════
#  EMISSÃO DE EVENTOS (canal da aba Monitoramento)
# ══════════════════════════════════════════════

def _log(msg: str, tipo: str = "muted"):
    _broadcast({"type": "mon_log", "msg": msg, "tipo": tipo})
    _registrar(f"[monitoramento] {msg}")


def _progresso(pct: int, texto: str, ok: bool = False, erro: bool = False):
    _broadcast({"type": "mon_progresso", "pct": pct, "texto": texto,
                "ok": ok, "erro": erro})


# ══════════════════════════════════════════════
#  API DO BLUECONTROL
# ══════════════════════════════════════════════

def _http_json(method: str, url: str, token: str, body: Optional[dict] = None,
               timeout: int = 10) -> Tuple[int, dict]:
    """Status 0 significa que não houve resposta HTTP nenhuma (falha de rede),
    o que a interface precisa distinguir de um 401/404 vindo do servidor."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Agent-Token", token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", str(e))
        except Exception:
            detail = str(e)
        return e.code, {"detail": detail}
    except urllib.error.URLError as e:
        return 0, {"detail": f"Não foi possível conectar em {url} ({e.reason})"}


def buscar(url_api: str, token: str, q: str) -> Tuple[int, dict]:
    """Procura máquinas já cadastradas. Devolve (status, corpo) porque a
    interface mostra o erro do BlueControl como ele veio."""
    alvo = (f"{url_api.rstrip('/')}/api/agente/equipamentos/buscar"
            f"?q={urllib.parse.quote(q or '')}")
    return _http_json("GET", alvo, token)


def cadastrar(url_api: str, token: str, dados: dict) -> Tuple[int, dict]:
    return _http_json("POST", f"{url_api.rstrip('/')}/api/agente/equipamentos",
                      token, dados)


# ══════════════════════════════════════════════
#  ESTADO ATUAL DA MÁQUINA
# ══════════════════════════════════════════════

def status() -> dict:
    """Config e estado do serviço nesta máquina. Os campos de URL/token voltam
    preenchidos mesmo sem config (existe=False): numa máquina nova eles são a
    única fonte para buscar e cadastrar."""
    existe = CONFIG_PATH.exists()
    cfg = {}
    if existe:
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

    servico = "desconhecido"
    try:
        r = subprocess.run(["sc", "query", SERVICE_NAME], capture_output=True,
                           text=True, creationflags=_NO_WINDOW)
        if "RUNNING" in r.stdout:
            servico = "rodando"
        elif "STOPPED" in r.stdout:
            servico = "parado"
        elif r.returncode != 0:
            servico = "nao_instalado"
    except Exception:
        pass

    return {
        "existe":     existe,
        "patrimonio": cfg.get("patrimonio", ""),
        "url_api":    (cfg.get("url_api") or cfg.get("api_url")
                       or (cfg.get("api_urls") or [DEFAULT_URL_API])[0]),
        # Sem padrão no código: um token conhecido na fonte vale tanto quanto
        # nenhum. Em máquina nova o técnico digita o token na interface.
        "token_agente": cfg.get("token_agente") or cfg.get("agent_secret") or "",
        "intervalo_segundos": cfg.get("intervalo_segundos", DEFAULT_INTERVALO),
        "servico":    servico,
        "agentes_disponiveis": {
            AGENTE_EXE: _origem_exe(AGENTE_EXE) is not None,
            REMOTE_EXE: _origem_exe(REMOTE_EXE) is not None,
        },
    }


# ══════════════════════════════════════════════
#  INSTALAÇÃO
# ══════════════════════════════════════════════

def _run_cmd(cmd: list, label: str) -> Tuple[bool, str]:
    """Versão em lista do _run_cmd do agente (que recebe string e passa pelo
    shell). Aqui os argumentos vêm de caminhos e nomes de serviço, então lista
    evita qualquer questão de escape."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           creationflags=_NO_WINDOW)
        ok = r.returncode == 0
        if not ok:
            _log(f"{label}: {(r.stderr or r.stdout or '').strip()[:200]}", "warn")
        return ok, r.stdout
    except Exception as e:
        _log(f"{label}: {e}", "err")
        return False, ""


def _origem_exe(nome: str) -> Optional[Path]:
    for padrao in _ORIGENS_EXE:
        cand = _resolver(padrao.format(nome=nome))
        if cand:
            return cand
    return None


def _instalar_servico(agente_path: Path) -> bool:
    """Registra o serviço com "sc create" direto — funciona porque o
    BlueControl_Metricas.exe implementa o protocolo de serviço do Windows de
    verdade, diferente do agente leve antigo, que só rodava um loop comum e o
    Windows derrubava sozinho."""
    subprocess.run(["sc", "stop", SERVICE_NAME], capture_output=True,
                   creationflags=_NO_WINDOW)
    time.sleep(1)
    subprocess.run(["sc", "delete", SERVICE_NAME], capture_output=True,
                   creationflags=_NO_WINDOW)
    time.sleep(1)

    ok, _ = _run_cmd(
        ["sc", "create", SERVICE_NAME, "binPath=", f'"{agente_path}"',
         "start=", "auto", "DisplayName=", SERVICE_DISP, "obj=", "LocalSystem"],
        "Criar serviço",
    )
    if not ok:
        return False
    subprocess.run(["sc", "description", SERVICE_NAME, SERVICE_DISP],
                   capture_output=True, creationflags=_NO_WINDOW)
    _run_cmd(["sc", "start", SERVICE_NAME], "Iniciar serviço")

    # "start" só confirma que o pedido foi aceito, não que o serviço continuou
    # de pé — confere de verdade com uma pausa curta.
    time.sleep(2)
    try:
        r = subprocess.run(["sc", "query", SERVICE_NAME], capture_output=True,
                           text=True, creationflags=_NO_WINDOW)
        return "RUNNING" in r.stdout
    except Exception:
        return False


def _habilitar_winrm() -> bool:
    """Liga o WinRM e libera a porta 5985 no firewall — sem isso o painel não
    consegue Bloquear/Reiniciar essa máquina remotamente (usa WinRM, não o
    agente de monitoramento)."""
    _run_cmd(["powershell", "-NoProfile", "-Command",
              "Enable-PSRemoting -Force -SkipNetworkProfileCheck"],
             "Habilitar WinRM")
    _run_cmd(["powershell", "-NoProfile", "-Command",
              "Set-Service WinRM -StartupType Automatic"],
             "Serviço WinRM automático")

    for regra in ("WinRM BlueControl", "Ping BlueControl"):
        subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule",
                        f"name={regra}"], capture_output=True,
                       creationflags=_NO_WINDOW)

    # Libera ping (ICMPv4 echo) — o Windows 11 bloqueia por padrão, e o NOC usa
    # ping pra mostrar online/offline.
    _run_cmd(["netsh", "advfirewall", "firewall", "add", "rule",
              "name=Ping BlueControl", "protocol=icmpv4:8,any",
              "dir=in", "action=allow"],
             "Liberar ping no firewall")

    ok, _ = _run_cmd(
        ["netsh", "advfirewall", "firewall", "add", "rule",
         "name=WinRM BlueControl", "protocol=TCP", "dir=in",
         f"localport={WINRM_PORTA}", "action=allow"],
        "Liberar porta no firewall",
    )
    return ok


def _instalar_acesso_remoto(origem: Path) -> bool:
    """Instala o acesso remoto como TAREFA AGENDADA no logon, não como serviço:
    serviço roda na sessão 0, que não enxerga a tela nem o mouse/teclado do
    usuário. A tarefa roda como o grupo Usuários (qualquer pessoa que logar)
    com privilégio mais alto, e é disparada na hora pra sessão já aberta — não
    precisa deslogar."""
    destino = INSTALL_DIR / REMOTE_EXE
    # Mata instância antiga antes de sobrescrever o .exe (arquivo em uso)
    subprocess.run(["taskkill", "/F", "/IM", REMOTE_EXE], capture_output=True,
                   creationflags=_NO_WINDOW)
    time.sleep(1)
    try:
        shutil.copy(str(origem), str(destino))
    except Exception as e:
        _log(f"Copiar agente remoto: {e}", "err")
        return False

    ps = f"""
$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName '{REMOTE_TASK}' -Confirm:$false -ErrorAction SilentlyContinue
$acao = New-ScheduledTaskAction -Execute '{destino}' -WorkingDirectory '{INSTALL_DIR}'
$gatilho = New-ScheduledTaskTrigger -AtLogOn
# S-1-5-32-545 = grupo Usuários local (SID independe do idioma do Windows)
$principal = New-ScheduledTaskPrincipal -GroupId 'S-1-5-32-545' -RunLevel Highest
$cfg = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName '{REMOTE_TASK}' -Action $acao -Trigger $gatilho -Principal $principal -Settings $cfg | Out-Null
Start-ScheduledTask -TaskName '{REMOTE_TASK}'
"""
    ok, _ = _run_cmd(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                      "-Command", ps], "Registrar tarefa do acesso remoto")
    if not ok:
        return False
    time.sleep(4)
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {REMOTE_EXE}"],
                       capture_output=True, text=True, creationflags=_NO_WINDOW)
    return REMOTE_EXE.lower() in r.stdout.lower()


def _parar_agente_antigo() -> None:
    """Máquina já cadastrada costuma ter o agente rodando e segurando o .exe.
    "sc stop" é assíncrono (e o .exe onefile tem processo filho), então espera
    o STOPPED de verdade e mata o que sobrar pelo nome."""
    subprocess.run(["sc", "stop", SERVICE_NAME], capture_output=True,
                   creationflags=_NO_WINDOW)
    for _ in range(15):
        r = subprocess.run(["sc", "query", SERVICE_NAME], capture_output=True,
                           text=True, creationflags=_NO_WINDOW)
        if r.returncode != 0 or "STOPPED" in r.stdout:
            break
        time.sleep(1)
    subprocess.run(["taskkill", "/F", "/T", "/IM", AGENTE_EXE],
                   capture_output=True, creationflags=_NO_WINDOW)

    if subprocess.run(["sc", "query", LEGADO_SERVICE], capture_output=True,
                      creationflags=_NO_WINDOW).returncode == 0:
        subprocess.run(["sc", "stop", LEGADO_SERVICE], capture_output=True,
                       creationflags=_NO_WINDOW)
        subprocess.run(["sc", "delete", LEGADO_SERVICE], capture_output=True,
                       creationflags=_NO_WINDOW)
        subprocess.run(["taskkill", "/F", "/T", "/IM", "BlueControl_Lite.exe"],
                       capture_output=True, creationflags=_NO_WINDOW)
        _log(f"Serviço antigo {LEGADO_SERVICE} removido", "info")
    time.sleep(1)


def _substituir_exe_travado(origem: Path, destino: Path) -> bool:
    _log("Parando agente antigo (se houver)…", "muted")
    _parar_agente_antigo()
    # Sobras do "renomeia e copia" de instalações anteriores
    for velho in INSTALL_DIR.glob(f"{destino.stem}.old*"):
        try:
            velho.unlink()
        except OSError:
            pass
    for _ in range(5):
        try:
            shutil.copy(str(origem), str(destino))
            return True
        except PermissionError:
            time.sleep(2)
    # Último recurso: o Windows deixa RENOMEAR um .exe em uso, só não sobrescrever.
    try:
        antigo = destino.with_suffix(f".old{int(time.time())}")
        destino.rename(antigo)
        shutil.copy(str(origem), str(destino))
        _log(f"Agente antigo ainda em uso — renomeado para {antigo.name}", "warn")
        return True
    except Exception as e:
        _log(f"Não consegui substituir {destino} (arquivo em uso): {e}", "err")
        return False


def instalar(params: dict) -> None:
    """Roda em thread separada: publica o andamento por SSE e não retorna nada."""
    try:
        patrimonio = (params.get("patrimonio") or "").strip()
        url_api    = (params.get("url_api") or "").strip().rstrip("/")
        token      = params.get("token_agente") or ""
        intervalo  = int(params.get("intervalo_segundos") or DEFAULT_INTERVALO)

        if not patrimonio or not url_api or not token:
            _log("Patrimônio, URL da API e token são obrigatórios.", "err")
            _progresso(100, "Dados incompletos", erro=True)
            return

        _progresso(10, "Criando C:\\BlueControl…")
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        _log(f"Pasta {INSTALL_DIR} pronta", "ok")

        _progresso(25, "Gravando config.json…")
        config = {
            "patrimonio": patrimonio,
            "url_api": url_api,
            "token_agente": token,
            "intervalo_segundos": intervalo,
            # Nomes que o BlueControl_Metricas.exe lê — mantém os dois pra
            # funcionar com agentes antigos e novos
            "api_urls": [url_api],
            "agent_secret": token,
        }
        CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False),
                               encoding="utf-8")
        _log(f"config.json gravado — patrimônio {patrimonio}", "ok")

        _progresso(40, "Copiando o agente…")
        agente_origem = _origem_exe(AGENTE_EXE)
        if not agente_origem:
            _log(f"{AGENTE_EXE} não encontrado — coloque a pasta agents\\ ao lado "
                 f"do AgenteBlue.exe ou use o build com os agentes embutidos", "err")
            _progresso(100, "Arquivo necessário não encontrado", erro=True)
            return

        agente_destino = INSTALL_DIR / AGENTE_EXE
        if not _substituir_exe_travado(agente_origem, agente_destino):
            _progresso(100, "Não consegui substituir o agente antigo", erro=True)
            return
        _log("Agente copiado — a máquina não precisa de Python instalado", "ok")

        _progresso(60, "Instalando serviço Windows…")
        servico_ok = _instalar_servico(agente_destino)
        if servico_ok:
            _log("Serviço de métricas instalado e rodando", "ok")
        else:
            _log("Serviço foi criado mas não ficou rodando — confira "
                 "C:\\BlueControl\\metricas.log", "err")

        _progresso(72, "Instalando acesso remoto…")
        remote_origem = _origem_exe(REMOTE_EXE)
        if not remote_origem:
            _log(f"{REMOTE_EXE} não encontrado — acesso remoto não instalado", "warn")
        elif _instalar_acesso_remoto(remote_origem):
            _log("Acesso remoto instalado e conectando (roda no logon de "
                 "qualquer usuário)", "ok")
        else:
            _log("Acesso remoto registrado mas não iniciou — confira "
                 "C:\\BlueControl\\remote.log", "warn")

        _progresso(85, "Habilitando WinRM (bloqueio/reinício remoto)…")
        if _habilitar_winrm():
            _log("WinRM habilitado — Bloquear/Reiniciar pelo painel já funciona "
                 "nessa máquina", "ok")
        else:
            _log("Não consegui habilitar o WinRM automaticamente — configure "
                 "manualmente depois", "warn")

        if servico_ok:
            _progresso(100, "Instalação concluída!", ok=True)
        else:
            _log(f"Rode direto pra ver o erro do agente: {agente_destino}", "info")
            _progresso(100, "Serviço de métricas não ficou de pé — veja o log acima",
                       erro=True)

    except Exception as e:
        _log(f"Erro inesperado: {e}", "err")
        _progresso(100, "Falha na instalação", erro=True)
