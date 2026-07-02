"""
programas.py — Agente Blue (v3.0)
=================================
Desinstalador em lote com limpeza de sobras ("lixo" no Program Files, AppData,
ProgramData e chaves de desinstalação órfãs).

Fontes da lista (as mesmas do "Adicionar ou Remover Programas"):
  • HKLM  \\...\\Uninstall\\*                (programas 64-bit)
  • HKLM  \\WOW6432Node\\...\\Uninstall\\*    (programas 32-bit)
  • HKCU  \\...\\Uninstall\\*                (programas por-usuário)
  • Get-AppxPackage                        (apps da Microsoft Store)

Desinstalação (decisões do usuário):
  • Silenciosa quando possível: QuietUninstallString → msiexec /qn → flags
    conhecidas (Inno /VERYSILENT, NSIS /S). Se ainda abrir janela, deixa rodar.
  • Limpeza SEGURA de sobras: remove InstallLocation + pastas em AppData/ProgramData
    com nome idêntico ao do app/publisher + a chave de desinstalação órfã. Nunca
    apaga raízes/pastas críticas (proteção em _pasta_segura).
  • Vários de uma vez = fila sequencial (o Windows Installer não permite MSIs em
    paralelo). Progresso ao vivo por app via SSE.

Como o usuarios.py, NÃO importa o agente.py: recebe os helpers via init().
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

# ── Helpers injetados pelo agente (ver init) ──────────────────────────────────
_log = lambda msg, tipo="muted": None
_broadcast = lambda ev: None
_logged_user = lambda: None
_NO_WINDOW = 0

# Catálogo da última listagem, indexado por id (preenchido em listar()).
# A desinstalação recebe apenas os ids e consulta aqui — não confia em dados
# arbitrários vindos do navegador.
_catalogo: dict = {}

# Códigos de saída tratados como sucesso em desinstaladores:
#   0=ok, 3010=ok+reiniciar, 1605=produto não instalado, 1614=já desinstalado.
_OK_CODES = {0, 3010, 1605, 1614}


def init(log, broadcast, logged_user, no_window):
    global _log, _broadcast, _logged_user, _NO_WINDOW
    _log = log
    _broadcast = broadcast
    _logged_user = logged_user
    _NO_WINDOW = no_window


# ══════════════════════════════════════════════════════════════════════════════
#  LISTAR
# ══════════════════════════════════════════════════════════════════════════════
def listar() -> dict:
    """Lista programas desktop (registro) + apps da Store, e preenche o catálogo."""
    global _catalogo
    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
$paths = @(
  'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$seen = @{}
$list = foreach ($p in $paths) {
  Get-ItemProperty $p -ErrorAction SilentlyContinue | ForEach-Object {
    $dn = $_.DisplayName
    if (-not $dn) { return }
    if ($_.SystemComponent -eq 1) { return }
    if ($_.ParentKeyName) { return }
    if ($_.ReleaseType -in @('Security Update','Update Rollup','Hotfix')) { return }
    $key = "$dn|$($_.DisplayVersion)"
    if ($seen.ContainsKey($key)) { return }
    $seen[$key] = $true
    $hive = if ($p -like 'HKLM*WOW6432*') {'HKLM32'} elseif ($p -like 'HKLM*') {'HKLM'} else {'HKCU'}
    [pscustomobject]@{
      id        = "reg|$hive|$($_.PSChildName)"
      nome      = $dn
      versao    = "$($_.DisplayVersion)"
      publisher = "$($_.Publisher)"
      local     = "$($_.InstallLocation)"
      uninstall = "$($_.UninstallString)"
      quiet     = "$($_.QuietUninstallString)"
      msi       = [bool]($_.WindowsInstaller -eq 1)
      tipo      = 'desktop'
    }
  }
}
$store = Get-AppxPackage | Where-Object { -not $_.IsFramework } | ForEach-Object {
  [pscustomobject]@{
    id        = "appx|$($_.PackageFullName)"
    nome      = $_.Name
    versao    = "$($_.Version)"
    publisher = "$($_.Publisher)"
    local     = "$($_.InstallLocation)"
    uninstall = ''
    quiet     = ''
    msi       = $false
    tipo      = 'store'
  }
}
@($list + $store) | Sort-Object nome | ConvertTo-Json -Compress -Depth 3
"""
    rc, out, err = _run_ps(ps, timeout=120)
    if rc != 0 and not out:
        return {"ok": False, "erro": err or "Falha ao listar programas."}
    try:
        data = json.loads(out) if out else []
        if isinstance(data, dict):
            data = [data]
    except Exception:
        return {"ok": False, "erro": "Não foi possível interpretar a lista de programas."}

    _catalogo = {item["id"]: item for item in data if item.get("id")}
    return {"ok": True, "programas": data}


# ══════════════════════════════════════════════════════════════════════════════
#  DESINSTALAR (lote, com progresso via SSE)
# ══════════════════════════════════════════════════════════════════════════════
def desinstalar_lote(ids: list) -> None:
    """Desinstala em fila os programas cujos ids foram selecionados, emitindo
    eventos SSE de progresso. Roda numa thread (disparada pelo endpoint)."""
    alvos = [_catalogo.get(i) for i in ids if _catalogo.get(i)]
    total = len(alvos)
    _broadcast({"type": "prog_inicio", "total": total})
    _log(f"[programas] Desinstalação em lote: {total} item(ns).", "info")

    ok_n = err_n = 0
    for item in alvos:
        nome = item.get("nome", "?")
        try:
            if item.get("tipo") == "store":
                sucesso = _desinstalar_store(item)
            else:
                sucesso = _desinstalar_desktop(item)
        except Exception as exc:
            _log(f"[programas] Exceção ao desinstalar {nome}: {exc}", "err")
            sucesso = False

        if sucesso:
            ok_n += 1
            _prog(nome, "ok", 100)
        else:
            err_n += 1
            _prog(nome, "erro", 100)

    _broadcast({"type": "prog_fim", "total": total, "ok": ok_n, "erro": err_n})
    _log(f"[programas] Concluído: {ok_n} ok, {err_n} com falha.", "ok" if err_n == 0 else "warn")


def _desinstalar_desktop(item: dict) -> bool:
    nome = item.get("nome", "?")
    _prog(nome, "desinstalando", 10)

    cmd, ok_codes = _cmd_desinstalar(item)
    if not cmd:
        _log(f"[programas] {nome}: sem desinstalador registrado — pulando.", "warn")
        _prog(nome, "manual", 100)
        return False

    _log(f"[programas] Desinstalando: {nome}", "info")
    sucesso = _run_cmd(cmd, ok_codes=ok_codes, timeout=1800)

    # Limpeza de sobras (segura), mesmo que o desinstalador retorne código != 0
    # (muitos retornam erro mas removem o grosso; a limpeza garante o resto).
    _prog(nome, "limpando", 70)
    removidos = _limpar_sobras(item)
    for r in removidos:
        _log(f"     • sobra removida: {r}", "muted")

    return sucesso


def _desinstalar_store(item: dict) -> bool:
    nome = item.get("nome", "?")
    _prog(nome, "desinstalando", 30)
    fullname = item["id"].split("|", 1)[1]
    _log(f"[programas] Removendo app da Store: {nome}", "info")
    ps = (
        f"$ErrorActionPreference='SilentlyContinue'; "
        f"Remove-AppxPackage -Package '{_psq(fullname)}' -AllUsers -ErrorAction SilentlyContinue; "
        f"Get-AppxProvisionedPackage -Online | Where-Object {{ $_.PackageName -eq '{_psq(fullname)}' }} "
        f"| ForEach-Object {{ Remove-AppxProvisionedPackage -Online -PackageName $_.PackageName "
        f"-ErrorAction SilentlyContinue | Out-Null }}"
    )
    rc, out, err = _run_ps(ps, timeout=300)
    return rc == 0


# ── Montagem do comando de desinstalação ──────────────────────────────────────
def _cmd_desinstalar(item: dict):
    """Retorna (comando, ok_codes) para desinstalar o item silenciosamente, ou
    (None, None) se não houver desinstalador."""
    uninstall = (item.get("uninstall") or "").strip()
    quiet = (item.get("quiet") or "").strip()

    # 1) MSI → msiexec /x {GUID} /qn (o mais confiável e 100% silencioso).
    if item.get("msi") or "msiexec" in uninstall.lower():
        m = re.search(r"\{[0-9A-Fa-f\-]{36}\}", uninstall)
        if m:
            return f'msiexec /x {m.group(0)} /qn /norestart', _OK_CODES

    # 2) QuietUninstallString (o próprio fornecedor já entrega o modo silencioso).
    if quiet:
        return quiet, _OK_CODES

    if not uninstall:
        return None, None

    # 3) Heurística por tipo de instalador.
    low = uninstall.lower()
    if "unins000" in low or "unins001" in low:          # Inno Setup
        return f'{uninstall} /VERYSILENT /SUPPRESSMSGBOXES /NORESTART', _OK_CODES
    # NSIS e a maioria dos demais aceitam /S. Se não for silencioso, a janela
    # simplesmente abre e o técnico conclui (decisão: "deixar rodar").
    return f'{uninstall} /S', _OK_CODES


# ── Limpeza de sobras (SEGURA) ────────────────────────────────────────────────
def _limpar_sobras(item: dict) -> list:
    removidos: list = []

    # 1) Pasta de instalação (o "lixo" clássico no Program Files).
    local = (item.get("local") or "").strip().rstrip("\\")
    if local and _pasta_segura(local) and os.path.isdir(local):
        shutil.rmtree(local, ignore_errors=True)
        if not os.path.isdir(local):
            removidos.append(local)

    # 2) Pastas em AppData/ProgramData com nome idêntico ao app ou ao publisher.
    info = _logged_user()
    profile = info["profile"] if info else os.environ.get("USERPROFILE", r"C:\Users\Default")
    roots = [
        os.path.join(profile, "AppData", "Local"),
        os.path.join(profile, "AppData", "Roaming"),
        r"C:\ProgramData",
    ]
    for cand in _nomes_candidatos(item):
        for root in roots:
            path = os.path.join(root, cand)
            if _pasta_segura(path) and os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
                if not os.path.isdir(path):
                    removidos.append(path)

    # 3) Chave de desinstalação órfã (best-effort).
    _remover_chave(item.get("id", ""))

    return removidos


def _nomes_candidatos(item: dict) -> list:
    """Nomes de pasta que removemos apenas se baterem EXATAMENTE (evita apagar
    dados de outros programas): o DisplayName e o Publisher, sanitizados."""
    out = []
    for v in (item.get("nome"), item.get("publisher")):
        v = (v or "").strip()
        # Remove caracteres inválidos de nome de pasta e espaços nas pontas.
        v = re.sub(r'[\\/:*?"<>|]', "", v).strip()
        if v and v not in out and len(v) >= 3:
            out.append(v)
    return out


def _remover_chave(item_id: str) -> None:
    if not item_id.startswith("reg|"):
        return
    try:
        _, hive, child = item_id.split("|", 2)
    except ValueError:
        return
    bases = {
        "HKLM":   r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKLM32": r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKCU":   r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    }
    base = bases.get(hive)
    if not base:
        return
    key = f"{base}\\{child}"
    subprocess.run(f'reg delete "{key}" /f', shell=True, capture_output=True,
                   creationflags=_NO_WINDOW)


# ── Proteção: só apaga pastas fundas sob raízes conhecidas ────────────────────
def _pasta_segura(path: str) -> bool:
    """True somente se 'path' for uma subpasta funda sob Program Files,
    Program Files (x86), ProgramData ou o AppData do usuário — nunca a própria
    raiz nem um diretório crítico. Blindagem contra rmtree catastrófico."""
    try:
        p = Path(path).resolve()
    except Exception:
        return False

    info = _logged_user()
    profile = info["profile"] if info else os.environ.get("USERPROFILE", r"C:\Users\Default")
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramData", r"C:\ProgramData"),
        os.path.join(profile, "AppData", "Local"),
        os.path.join(profile, "AppData", "Roaming"),
    ]

    s_parts = [x.lower() for x in p.parts]
    for root in roots:
        try:
            rp = Path(root).resolve()
        except Exception:
            continue
        r_parts = [x.lower() for x in rp.parts]
        # Precisa estar DENTRO da raiz e ter ao menos 1 nível a mais (não ser a raiz).
        if len(s_parts) > len(r_parts) and s_parts[:len(r_parts)] == r_parts:
            return True
    return False


# ── Utilitários internos (mesma linha do usuarios.py) ─────────────────────────
def _psq(s: str) -> str:
    return str(s).replace("'", "''")


def _script_dir() -> Path:
    d = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "AgenteBlue"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_ps(script: str, timeout: int = 120) -> tuple:
    ps_file = _script_dir() / "programas_op.ps1"
    ps_file.write_text(script, encoding="utf-8-sig")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_file)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=_NO_WINDOW,
        )
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 1, "", "Tempo esgotado."
    except Exception as exc:
        return 1, "", str(exc)


def _run_cmd(cmd: str, ok_codes: Optional[set] = None, timeout: int = 1800) -> bool:
    """Roda um desinstalador. detach (DEVNULL) porque alguns processos ficam
    persistentes e travariam um pipe capturado."""
    if ok_codes is None:
        ok_codes = _OK_CODES
    try:
        r = subprocess.run(
            cmd, shell=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout, creationflags=_NO_WINDOW,
        )
        return r.returncode in ok_codes
    except subprocess.TimeoutExpired:
        _log("  ✗  Timeout na desinstalação.", "err")
        return False
    except Exception as exc:
        _log(f"  ✗  Exceção na desinstalação: {exc}", "err")
        return False


def _prog(nome: str, estado: str, pct: int = 0) -> None:
    """Evento SSE de progresso: estado ∈ desinstalando|limpando|ok|erro|manual."""
    _broadcast({"type": "prog_uninstall", "nome": nome, "estado": estado, "pct": pct})
