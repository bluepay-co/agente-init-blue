"""
BlueControl IT — Agente de Comandos para macOS
================================================
Roda como LaunchDaemon (root). A cada 30 segundos consulta a API
do servidor para verificar se há um comando pendente (bloquear/desbloquear).
Faz polling HTTP direto para o servidor.

Instalar:
  sudo cp agente_comandos_mac.py /usr/local/bluecontrol/
  sudo cp com.bluecontrol.comandos.plist /Library/LaunchDaemons/
  sudo launchctl bootstrap system /Library/LaunchDaemons/com.bluecontrol.comandos.plist
"""

import json
import logging
import secrets
import shlex
import subprocess
import sys
import time
import io as _io
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

# ─── Caminhos ──────────────────────────────────────────────────────────────
BASE_DIR    = Path("/usr/local/bluecontrol")
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH    = BASE_DIR / "comandos.log"

POLL_INTERVALO = 30   # segundos entre polls
TIMEOUT_HTTP   = 10   # timeout das requisições

# ─── Logging ───────────────────────────────────────────────────────────────
BASE_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("bluecontrol_comandos_mac")
try:
    _con = logging.StreamHandler(
        _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    )
except AttributeError:
    _con = logging.StreamHandler(sys.stdout)
_con.setLevel(logging.INFO)
_con.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
log.addHandler(_con)

# ─── Scripts de bloqueio/desbloqueio ───────────────────────────────────────
# Rodamos como root (LaunchDaemon), não precisa de sudo
#
# As senhas NÃO ficam nos scripts: entram em runtime por montar_script(), vindas
# do config.json ou sorteadas. Senha versionada no Git vale tanto quanto nenhuma.
#
# Marcas @@...@@ em vez de str.format(): estes scripts são shell e estão cheios
# de "$" e "{}", que o format interpretaria. A substituição passa por
# shlex.quote, senão uma senha com " ou $ escaparia das aspas e viraria comando.
_MARCA_LOCKOUT = "@@SENHA_LOCKOUT@@"   # sorteada: trava o usuário para fora
_MARCA_ADMIN   = "@@SENHA_ADMIN@@"     # conta "bluecontrol" (config.json)
_MARCA_TEMP    = "@@SENHA_TEMP@@"      # senha que o usuário recebe de volta

SCRIPT_BLOQUEIO = r"""
CONSOLE_USER=$(stat -f "%Su" /dev/console 2>/dev/null)
if [ -n "$CONSOLE_USER" ] && [ "$CONSOLE_USER" != "root" ] && [ "$CONSOLE_USER" != "loginwindow" ]; then
    CONSOLE_UID=$(id -u "$CONSOLE_USER" 2>/dev/null)
    echo "TRAVANDO_TELA:$CONSOLE_USER"
    # macOS 15 Sequoia: CGSession foi removido pela Apple.
    # Força logout encerrando todos os processos do usuário ativo.
    pkill -KILL -u "$CONSOLE_USER" 2>/dev/null || true
    sleep 1
    # Segunda passagem: garante que processos remanescentes sejam encerrados
    ps -u "$CONSOLE_USER" -o pid= 2>/dev/null | xargs kill -9 2>/dev/null || true
fi

for user in $(dscl . -list /Users | grep -v '^_' | grep -v 'root\|nobody\|daemon\|Guest\|bluecontrol\|admin'); do
    echo "NEUTRALIZANDO:$user"
    sysadminctl -resetPasswordFor "$user" -newPassword @@SENHA_LOCKOUT@@ 2>/dev/null || \
        dscl . -passwd /Users/$user @@SENHA_LOCKOUT@@ 2>/dev/null || true
    dscl . -create /Users/$user UserShell /usr/bin/false 2>/dev/null || true
    sysadminctl -disableUser "$user" 2>/dev/null || \
        dscl . -create /Users/$user AuthenticationAuthority ";DisabledUser;" 2>/dev/null || true
done

defaults write /Library/Preferences/com.apple.loginwindow LoginwindowText \
    "ACESSO REVOGADO. Este equipamento foi bloqueado pela TI. Devolva imediatamente." 2>/dev/null || true
defaults write /Library/Preferences/com.apple.loginwindow DisableConsoleAccess -bool true 2>/dev/null || true

echo "SUCESSO_BLOQUEIO"
"""

SCRIPT_DESBLOQUEIO = r"""
for user in $(dscl . -list /Users | grep -v '^_' | grep -v 'root\|nobody\|daemon\|Guest\|bluecontrol\|admin'); do
    echo "REATIVANDO:$user"
    # Restaura shell (bloqueio define como /usr/bin/false)
    dscl . -create /Users/$user UserShell /bin/zsh 2>/dev/null || true
    # NÃO deletar AuthenticationAuthority — o bloqueio não a modificou,
    # deletar destruiria o hash da senha e impediria o login.
    sysadminctl -enableUser "$user" -adminUser "bluecontrol" -adminPassword @@SENHA_ADMIN@@ 2>/dev/null || \
        sysadminctl -enableUser "$user" 2>/dev/null || true
    sysadminctl -resetPasswordFor "$user" -newPassword @@SENHA_TEMP@@ \
        -adminUser "bluecontrol" -adminPassword @@SENHA_ADMIN@@ 2>/dev/null || \
        dscl . -passwd /Users/$user @@SENHA_TEMP@@ 2>/dev/null || true
    dscl . -append /Groups/staff GroupMembership "$user" 2>/dev/null || true
done

defaults delete /Library/Preferences/com.apple.loginwindow LoginwindowText 2>/dev/null || true
defaults delete /Library/Preferences/com.apple.loginwindow DisableConsoleAccess 2>/dev/null || true
killall -HUP opendirectoryd 2>/dev/null || true

echo "SUCESSO_DESBLOQUEIO"
"""


# ─── Funções ───────────────────────────────────────────────────────────────

def carregar_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config não encontrado: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def executar_script(script: str, tipo: str) -> tuple[bool, str]:
    """Executa o script bash localmente (como root) e retorna (sucesso, saida)."""
    try:
        resultado = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            timeout=90
        )
        saida = resultado.stdout + resultado.stderr
        token_ok = f"SUCESSO_{tipo.upper()}" in saida
        sucesso   = token_ok or resultado.returncode == 0
        log.info(f"Script {tipo} rc={resultado.returncode} sucesso={sucesso}")
        if resultado.stderr:
            log.warning(f"stderr: {resultado.stderr[:300]}")
        return sucesso, saida[:500]
    except subprocess.TimeoutExpired:
        log.error(f"Timeout ao executar script {tipo}")
        return False, "Timeout"
    except Exception as e:
        log.error(f"Erro ao executar script {tipo}: {e}")
        return False, str(e)


def _http_get(url: str, token: str) -> dict:
    """GET com urllib — sem dependências externas."""
    req = urllib.request.Request(url, headers={"X-Agent-Token": token})
    with urllib.request.urlopen(req, timeout=TIMEOUT_HTTP) as resp:
        return json.loads(resp.read().decode())

def _http_post(url: str, token: str, payload: dict) -> dict:
    """POST JSON com urllib."""
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=data,
        headers={"X-Agent-Token": token, "Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_HTTP) as resp:
        return json.loads(resp.read().decode())


def _senha_obrigatoria(cfg: dict, chave: str) -> str:
    """Senha vinda só do config.json, como o token. Sem padrão no código."""
    valor = cfg.get(chave)
    if not valor:
        raise SystemExit(
            f"[config] '{chave}' ausente no config.json do agente "
            f"({CONFIG_PATH}) — bloqueio/desbloqueio remoto não pode rodar."
        )
    return valor


def montar_script(tmpl: str, cfg: dict) -> str:
    """Troca as marcas @@...@@ pelas senhas, já escapadas para o shell.

    A senha de lockout é sorteada a cada bloqueio: ela existe só para o usuário
    não conseguir mais entrar, ninguém precisa conhecê-la, e fixa no código
    bastaria alguém ler o repositório para destravar qualquer máquina.
    """
    pronto = tmpl
    if _MARCA_LOCKOUT in pronto:
        lockout = "Bl!" + secrets.token_urlsafe(24)
        pronto = pronto.replace(_MARCA_LOCKOUT, shlex.quote(lockout))
    if _MARCA_ADMIN in pronto:
        pronto = pronto.replace(
            _MARCA_ADMIN, shlex.quote(_senha_obrigatoria(cfg, "senha_admin_local")))
    if _MARCA_TEMP in pronto:
        pronto = pronto.replace(
            _MARCA_TEMP, shlex.quote(_senha_obrigatoria(cfg, "senha_temporaria")))
    return pronto


def _token_obrigatorio(cfg: dict) -> str:
    """Token do agente, vindo so do config. Sem padrao: um token conhecido no
    codigo-fonte vale tanto quanto nenhum."""
    token = cfg.get("agent_secret") or cfg.get("token_agente")
    if not token:
        raise SystemExit("[config] 'agent_secret' ausente no config.json do agente.")
    return token


def poll_uma_vez(cfg: dict):
    """Consulta a API e executa um comando se houver."""
    servidor   = cfg["servidor_ip"]
    porta      = cfg.get("servidor_porta", 8550)
    patrimonio = cfg["patrimonio"]
    token      = _token_obrigatorio(cfg)
    base_url   = f"http://{servidor}:{porta}"

    # Consulta comando pendente
    try:
        params = urllib.parse.urlencode({"patrimonio": patrimonio})
        data = _http_get(f"{base_url}/api/agente/mac/comando?{params}", token)
    except urllib.error.URLError:
        # Servidor inacessível — normal quando fora da rede sem rota para o servidor
        return
    except Exception as e:
        log.warning(f"Erro ao consultar API: {e}")
        return

    if not data.get("comando"):
        return  # Nada a fazer

    cmd_id = data["id"]
    tipo   = data["comando"]  # "bloquear" ou "desbloquear"
    log.info(f"Comando recebido: {tipo} (id={cmd_id})")

    # Executa o script correto
    if tipo == "bloquear":
        sucesso, saida = executar_script(montar_script(SCRIPT_BLOQUEIO, cfg), "bloqueio")
    elif tipo == "desbloquear":
        sucesso, saida = executar_script(montar_script(SCRIPT_DESBLOQUEIO, cfg), "desbloqueio")
    else:
        log.warning(f"Tipo de comando desconhecido: {tipo}")
        sucesso, saida = False, f"Tipo desconhecido: {tipo}"

    # Reporta resultado de volta — com retry (pkill pode derrubar a rede por instantes)
    payload = {"id": cmd_id, "sucesso": sucesso, "saida": saida}
    for tentativa in range(1, 6):   # até 5 tentativas
        try:
            _http_post(f"{base_url}/api/agente/mac/resultado", token, payload)
            log.info(f"Resultado reportado: sucesso={sucesso} (tentativa {tentativa})")
            break
        except Exception as e:
            log.warning(f"Tentativa {tentativa}/5 falhou ao reportar resultado: {e}")
            if tentativa < 5:
                time.sleep(20)   # aguarda 20s antes de tentar novamente
    else:
        log.error("Todas as tentativas de reportar resultado falharam.")


# ─── Loop principal ─────────────────────────────────────────────────────────

def main():
    log.info("=== BlueControl Agente de Comandos macOS iniciado ===")

    cfg = carregar_config()
    log.info(f"Patrimônio: {cfg['patrimonio']} | Servidor: {cfg['servidor_ip']} | Intervalo: {POLL_INTERVALO}s")

    while True:
        try:
            poll_uma_vez(cfg)
        except Exception as e:
            log.error(f"Erro inesperado no loop: {e}")
        time.sleep(POLL_INTERVALO)


if __name__ == "__main__":
    main()
