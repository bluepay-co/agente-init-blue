"""
usuarios.py — Agente Blue (v3.0)
================================
Gerenciamento de usuários locais do Windows: listar, criar, editar
(renomear + trocar senha) e excluir — pela interface do agente.

Este módulo NÃO importa o agente.py diretamente (evita reimportar o __main__).
Em vez disso, o agente injeta os helpers de que precisamos via `init()`:
  • log         → registra no log em arquivo do agente
  • logged_user → dict do usuário logado no console (ou None)
  • no_window   → flag CREATE_NO_WINDOW (não pisca janelas de console)

Ponto delicado (renomear): no Windows o que liga a conta ao perfil é o valor
`ProfileImagePath` em HKLM\\...\\ProfileList\\<SID>. Renomear só a pasta quebra o
perfil (vira temporário). Por isso a renomeação troca as TRÊS coisas em sincronia:
conta (Rename-LocalUser, mantém o mesmo SID), pasta e ProfileImagePath.

Estratégia híbrida de renomeação:
  • alvo DESLOGADO (hive não carregado) → renomeia na hora.
  • alvo LOGADO (hive travado) → agenda a renomeação para o próximo boot como
    SYSTEM (antes de qualquer login) e reinicia — 100% seguro, sem perfil temporário.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

# ── Helpers injetados pelo agente (ver init) ──────────────────────────────────
_log = lambda msg, tipo="muted": None
_logged_user = lambda: None
_NO_WINDOW = 0

# Grupo de Administradores pelo SID well-known (independe do idioma do Windows).
_ADMIN_SID = "S-1-5-32-544"
# Nome fixo da tarefa agendada usada na renomeação em boot.
_TASK_RENAME = "AgenteBlue_RenameBoot"


def init(log, logged_user, no_window):
    """Chamado uma vez pelo agente para injetar os helpers compartilhados."""
    global _log, _logged_user, _NO_WINDOW
    _log = log
    _logged_user = logged_user
    _NO_WINDOW = no_window


# ── Diretório de scripts (mesmo padrão das outras etapas do agente) ────────────
def _script_dir() -> Path:
    d = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "AgenteBlue"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _psq(s: str) -> str:
    """Escapa uma string para literal single-quoted do PowerShell (anti-injeção)."""
    return str(s).replace("'", "''")


def _run_ps(script: str, timeout: int = 120) -> tuple:
    """Grava o script num .ps1 (utf-8-sig p/ acentos no PS 5.1) e executa.
    Retorna (returncode, stdout, stderr)."""
    ps_file = _script_dir() / "usuarios_op.ps1"
    ps_file.write_text(script, encoding="utf-8-sig")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_file)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=_NO_WINDOW,
        )
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 1, "", "Tempo esgotado ao executar a operação."
    except Exception as exc:  # defensivo
        return 1, "", str(exc)


# ── Validação de nome ─────────────────────────────────────────────────────────
_INVALIDOS = re.compile(r'[\\/:*?"<>|]')


def _validar_nome(nome: str) -> Optional[str]:
    """Retorna uma mensagem de erro se o nome for inválido, ou None se ok.
    Regras: sem caracteres proibidos de pasta/conta, sem terminar em espaço/ponto,
    1..20 caracteres (limite de conta local SAM)."""
    if not nome or not nome.strip():
        return "Informe um nome de usuário."
    if len(nome) > 20:
        return "O nome deve ter no máximo 20 caracteres."
    if _INVALIDOS.search(nome):
        return 'O nome não pode conter: \\ / : * ? " < > |'
    if nome[-1] in (" ", "."):
        return "O nome não pode terminar com espaço ou ponto."
    return None


# ── Salvaguardas ──────────────────────────────────────────────────────────────
def _protegido(sid: str) -> Optional[str]:
    """Retorna o motivo se o usuário não puder ser alterado, ou None."""
    if re.match(r"^S-1-5-21-.*-500$", sid or ""):
        return "É a conta interna de Administrador do Windows (protegida)."
    info = _logged_user()
    if info and info.get("sid") == sid:
        return "É o usuário logado agora no computador. Faça logoff dele antes."
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  LISTAR
# ══════════════════════════════════════════════════════════════════════════════
def listar() -> dict:
    """Lista os usuários locais reais (oculta contas de sistema) com: nome, sid,
    se é admin, pasta de perfil, se está logado (hive carregado) e se é a conta
    interna de Administrador."""
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$admGroup = (Get-LocalGroup -SID '{_ADMIN_SID}').Name
$admins = @(Get-LocalGroupMember -Group $admGroup | ForEach-Object {{ $_.SID.Value }})
$out = foreach ($u in Get-LocalUser) {{
  $sid = $u.SID.Value
  # Oculta contas de sistema pelo SID (independe do idioma): Convidado/Guest (-501),
  # DefaultAccount (-503) e WDAGUtilityAccount (-504). Mantém o Administrador (-500).
  if ($sid -match '-50[134]$') {{ continue }}
  $reg = "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList\\$sid"
  $prof = (Get-ItemProperty $reg -ErrorAction SilentlyContinue).ProfileImagePath
  $loaded = Test-Path "Registry::HKEY_USERS\\$sid"
  [pscustomobject]@{{
    nome    = $u.Name
    sid     = $sid
    enabled = [bool]$u.Enabled
    admin   = ($admins -contains $sid)
    builtin = ($sid -like 'S-1-5-21-*-500')
    profile = $prof
    logado  = [bool]$loaded
  }}
}}
@($out) | ConvertTo-Json -Compress -Depth 3
"""
    rc, out, err = _run_ps(script, timeout=60)
    if rc != 0 and not out:
        return {"ok": False, "erro": err or "Falha ao listar usuários."}
    try:
        data = json.loads(out) if out else []
        if isinstance(data, dict):
            data = [data]
    except Exception:
        return {"ok": False, "erro": "Não foi possível interpretar a lista de usuários."}
    return {"ok": True, "usuarios": data}


# ══════════════════════════════════════════════════════════════════════════════
#  CRIAR
# ══════════════════════════════════════════════════════════════════════════════
def criar(nome: str, senha: str = "", admin: bool = False) -> dict:
    erro = _validar_nome(nome)
    if erro:
        return {"ok": False, "erro": erro}

    n = _psq(nome)
    if senha:
        senha_linha = f"$sec = ConvertTo-SecureString '{_psq(senha)}' -AsPlainText -Force"
        cria = (f"New-LocalUser -Name '{n}' -FullName '{n}' -Password $sec "
                f"-PasswordNeverExpires -AccountNeverExpires -ErrorAction Stop")
    else:
        senha_linha = ""
        cria = (f"New-LocalUser -Name '{n}' -FullName '{n}' -NoPassword "
                f"-AccountNeverExpires -ErrorAction Stop")

    add_admin = (f"Add-LocalGroupMember -Group (Get-LocalGroup -SID '{_ADMIN_SID}').Name "
                 f"-Member '{n}' -ErrorAction Stop") if admin else ""

    script = f"""
$ErrorActionPreference = 'Stop'
try {{
  if (Get-LocalUser -Name '{n}' -ErrorAction SilentlyContinue) {{ throw 'Já existe um usuário com esse nome.' }}
  {senha_linha}
  {cria} | Out-Null
  {add_admin}
  Write-Output 'OK'
}} catch {{
  Write-Error $_.Exception.Message
  exit 1
}}
"""
    rc, out, err = _run_ps(script, timeout=90)
    if rc == 0:
        _log(f"[usuarios] Criado: {nome} (admin={admin})", "ok")
        return {"ok": True, "msg": f"Usuário '{nome}' criado com sucesso."}
    _log(f"[usuarios] Falha ao criar {nome}: {err}", "warn")
    return {"ok": False, "erro": err or "Falha ao criar o usuário."}


# ══════════════════════════════════════════════════════════════════════════════
#  EDITAR  (renomear e/ou trocar senha)
# ══════════════════════════════════════════════════════════════════════════════
def editar(sid: str, novo_nome: str = "", nova_senha: str = "") -> dict:
    if not sid:
        return {"ok": False, "erro": "SID do usuário não informado."}
    if not nova_senha and not novo_nome:
        return {"ok": False, "erro": "Nada para alterar."}

    # Trocar senha (feito ANTES do rename, enquanto o nome ainda é o atual).
    # Bloqueia só a conta interna de Administrador; trocar a senha do usuário
    # logado é permitido (é inofensivo e útil).
    if nova_senha:
        if re.match(r"^S-1-5-21-.*-500$", sid):
            return {"ok": False, "erro": "É a conta interna de Administrador do Windows (protegida)."}
        r = _trocar_senha(sid, nova_senha)
        if not r["ok"]:
            return r

    # Renomear (conta + pasta + registro), se solicitado.
    if novo_nome:
        return renomear(sid, novo_nome)

    return {"ok": True, "msg": "Senha atualizada com sucesso."}


def _trocar_senha(sid: str, nova_senha: str) -> dict:
    script = f"""
$ErrorActionPreference = 'Stop'
try {{
  $u = Get-LocalUser | Where-Object {{ $_.SID.Value -eq '{_psq(sid)}' }}
  if (-not $u) {{ throw 'Usuário não encontrado.' }}
  $sec = ConvertTo-SecureString '{_psq(nova_senha)}' -AsPlainText -Force
  Set-LocalUser -Name $u.Name -Password $sec
  Write-Output 'OK'
}} catch {{
  Write-Error $_.Exception.Message
  exit 1
}}
"""
    rc, out, err = _run_ps(script, timeout=60)
    if rc == 0:
        _log("[usuarios] Senha atualizada.", "ok")
        return {"ok": True, "msg": "Senha atualizada."}
    return {"ok": False, "erro": err or "Falha ao trocar a senha."}


# ══════════════════════════════════════════════════════════════════════════════
#  RENOMEAR  (o núcleo — lógica híbrida)
# ══════════════════════════════════════════════════════════════════════════════
def _estado(sid: str) -> Optional[dict]:
    """Retorna {nome, profile, loaded} do usuário pelo SID, ou None."""
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$u = Get-LocalUser | Where-Object {{ $_.SID.Value -eq '{_psq(sid)}' }}
if (-not $u) {{ exit 1 }}
$reg = "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList\\{_psq(sid)}"
$prof = (Get-ItemProperty $reg -ErrorAction SilentlyContinue).ProfileImagePath
$loaded = Test-Path "Registry::HKEY_USERS\\{_psq(sid)}"
[pscustomobject]@{{ nome=$u.Name; profile=$prof; loaded=[bool]$loaded }} | ConvertTo-Json -Compress
"""
    rc, out, err = _run_ps(script, timeout=45)
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def _script_rename(sid: str, novo: str, self_delete_task: bool = False) -> str:
    """Gera o PowerShell que renomeia conta + pasta + ProfileImagePath.
    self_delete_task=True: usado na versão de boot; apaga a tarefa agendada ao fim."""
    limpar = f"schtasks /delete /tn {_TASK_RENAME} /f 2>$null" if self_delete_task else ""
    return f"""
$ErrorActionPreference = 'Stop'
$sid  = '{_psq(sid)}'
$novo = '{_psq(novo)}'
try {{
  $u = Get-LocalUser | Where-Object {{ $_.SID.Value -eq $sid }}
  if (-not $u) {{ throw 'Usuário não encontrado.' }}
  $atual = $u.Name
  $reg = "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList\\$sid"
  $old = (Get-ItemProperty $reg -ErrorAction SilentlyContinue).ProfileImagePath

  # Valida a pasta destino ANTES de mexer em qualquer coisa (evita estado inconsistente).
  $new = $null
  if ($old -and (Test-Path $old)) {{
    $new = Join-Path (Split-Path $old -Parent) $novo
    if (Test-Path $new) {{ throw "Já existe a pasta $new" }}
  }}

  # 1) Renomeia a CONTA (mantém o mesmo SID → nada quebra).
  Rename-LocalUser -Name $atual -NewName $novo

  # 2/3) Se há perfil em disco, renomeia a PASTA e corrige o REGISTRO.
  if ($new) {{
    Rename-Item -LiteralPath $old -NewName $novo
    Set-ItemProperty -Path $reg -Name ProfileImagePath -Value $new
  }}
  Write-Output 'OK'
}} catch {{
  Write-Error $_.Exception.Message
  {limpar}
  exit 1
}}
{limpar}
"""


def renomear(sid: str, novo_nome: str) -> dict:
    if not sid:
        return {"ok": False, "erro": "SID do usuário não informado."}
    erro = _validar_nome(novo_nome)
    if erro:
        return {"ok": False, "erro": erro}

    motivo = _protegido(sid)
    if motivo:
        return {"ok": False, "erro": motivo}

    est = _estado(sid)
    if not est:
        return {"ok": False, "erro": "Usuário não encontrado."}

    if est.get("nome") == novo_nome:
        return {"ok": False, "erro": "O novo nome é igual ao atual."}

    # Colisão de conta / pasta (checagem rápida em Python).
    novo_dir = Path(r"C:\Users") / novo_nome
    if est.get("profile") and novo_dir.exists():
        return {"ok": False, "erro": f"Já existe a pasta {novo_dir}."}

    # ── Caso normal: alvo DESLOGADO → renomeia na hora ────────────────────────
    if not est.get("loaded"):
        script = _script_rename(sid, novo_nome)
        rc, out, err = _run_ps(script, timeout=120)
        if rc == 0:
            _log(f"[usuarios] Renomeado (imediato): {est.get('nome')} → {novo_nome}", "ok")
            return {"ok": True, "modo": "imediato",
                    "msg": f"Usuário renomeado para '{novo_nome}'. Pasta e login atualizados."}
        _log(f"[usuarios] Falha ao renomear: {err}", "warn")
        return {"ok": False, "erro": err or "Falha ao renomear o usuário."}

    # ── Alvo LOGADO: agenda no próximo boot (SYSTEM) e reinicia ────────────────
    return _renomear_no_boot(sid, novo_nome, est.get("nome", ""))


def _renomear_no_boot(sid: str, novo_nome: str, nome_atual: str) -> dict:
    boot_script = _script_dir() / "rename_boot.ps1"
    try:
        boot_script.write_text(_script_rename(sid, novo_nome, self_delete_task=True),
                               encoding="utf-8-sig")
    except Exception as exc:
        return {"ok": False, "erro": f"Falha ao preparar a renomeação: {exc}"}

    subprocess.run(f"schtasks /delete /tn {_TASK_RENAME} /f",
                   shell=True, capture_output=True, creationflags=_NO_WINDOW)
    # O caminho fica em ProgramData\AgenteBlue (sem espaços), então dispensamos
    # aspas internas — que são problemáticas de escapar no /tr do schtasks.
    tr = f"powershell -NoProfile -ExecutionPolicy Bypass -File {boot_script}"
    criar_task = (f'schtasks /create /tn {_TASK_RENAME} /tr "{tr}" '
                  f'/sc ONSTART /ru SYSTEM /rl highest /f')
    r = subprocess.run(criar_task, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if r.returncode != 0:
        return {"ok": False, "erro": (r.stderr or "").strip() or "Falha ao agendar a renomeação."}

    _log(f"[usuarios] Renomeação agendada no boot: {nome_atual} → {novo_nome}. Reiniciando.", "info")
    subprocess.run('shutdown /r /t 20 /c "Agente Blue: concluindo renomeacao de perfil"',
                   shell=True, capture_output=True, creationflags=_NO_WINDOW)
    return {"ok": True, "modo": "reboot",
            "msg": (f"O usuário '{nome_atual}' está logado, então a renomeação para "
                    f"'{novo_nome}' será concluída com segurança no próximo boot. "
                    f"O computador vai reiniciar em ~20 segundos.")}


# ══════════════════════════════════════════════════════════════════════════════
#  EXCLUIR
# ══════════════════════════════════════════════════════════════════════════════
def excluir(sid: str, apagar_perfil: bool = False) -> dict:
    if not sid:
        return {"ok": False, "erro": "SID do usuário não informado."}

    motivo = _protegido(sid)
    if motivo:
        return {"ok": False, "erro": motivo}

    est = _estado(sid)
    if not est:
        return {"ok": False, "erro": "Usuário não encontrado."}
    if apagar_perfil and est.get("loaded"):
        return {"ok": False, "erro": "O usuário está logado. Faça logoff para apagar a pasta do perfil."}

    apaga = ""
    if apagar_perfil:
        apaga = (f"Get-CimInstance Win32_UserProfile -Filter \"SID='{_psq(sid)}'\" "
                 f"-ErrorAction SilentlyContinue | Remove-CimInstance -ErrorAction SilentlyContinue")

    script = f"""
$ErrorActionPreference = 'Stop'
try {{
  $u = Get-LocalUser | Where-Object {{ $_.SID.Value -eq '{_psq(sid)}' }}
  if (-not $u) {{ throw 'Usuário não encontrado.' }}
  {apaga}
  Remove-LocalUser -SID '{_psq(sid)}'
  Write-Output 'OK'
}} catch {{
  Write-Error $_.Exception.Message
  exit 1
}}
"""
    rc, out, err = _run_ps(script, timeout=90)
    if rc == 0:
        _log(f"[usuarios] Excluído: {est.get('nome')} (perfil apagado={apagar_perfil})", "ok")
        return {"ok": True, "msg": f"Usuário '{est.get('nome')}' excluído."}
    return {"ok": False, "erro": err or "Falha ao excluir o usuário."}