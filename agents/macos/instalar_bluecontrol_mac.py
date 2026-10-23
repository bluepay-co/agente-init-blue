#!/usr/bin/env python3
"""
BlueControl IT — Instalador Mac
================================
Interface HTML idêntica ao instalador Windows, via pywebview (WebKit nativo).

Funcionalidades:
  - Torna TODOS os usuários da máquina administradores
  - Instala agente VPN com detecção por MAC do gateway
  - Instala agente de métricas como LaunchDaemon
  - Watchdog que recarrega serviços se forem parados
  - Proteção por senha IT (bluecontrol-parar exige a senha)
  - SSH habilitado
  - VPN L2TP/IPSec criada no SystemConfiguration

Compilar:
  pip install pyinstaller pywebview
  pyinstaller --onefile --noconsole \\
    --hidden-import=webview \\
    --collect-all webview \\
    --name "BlueControl_Agente_Mac" instalar_bluecontrol_mac.py
"""

import os, sys, json, stat, subprocess, hashlib, tempfile, threading
import webview

# ── CONFIGURAÇÕES ─────────────────────────────────────────────────────────────
SERVIDOR_IP    = "192.168.0.17"
SERVIDOR_PORTA = 8550
# Segredos vêm do ambiente — nunca do código. Defina antes de rodar:
#   export BLUECONTROL_AGENT_TOKEN=... BLUECONTROL_VPN_PSK=...
AGENT_SECRET   = os.environ.get("BLUECONTROL_AGENT_TOKEN", "")
VPN_NAME       = "VPN_BLUEPAY"
VPN_SERVIDOR   = "187.102.159.62"
VPN_PSK        = os.environ.get("BLUECONTROL_VPN_PSK", "")
GATEWAY_MAC_BP = "C8:4F:86:D4:4F:DF"   # MAC fixo do roteador Bluepay
BASE_DIR       = "/usr/local/bluecontrol"
# Usuário de serviço + chave pública do servidor (comandos remotos via SSH:
# desligar/reiniciar/bloquear/pop-up). A chave PRIVADA fica só no servidor.
SSH_USER       = "bluecontrol"
SSH_PUBKEY     = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAaoGuVj+GbfMLfwpBcdhZQs+jTr7oxjGOkKq+9pnXtx bluecontrol-mac-20260916"
PLIST_VPN      = "/Library/LaunchDaemons/com.bluecontrol.agente.plist"
PLIST_MTR      = "/Library/LaunchDaemons/com.bluecontrol.metricas.plist"
PLIST_WDG      = "/Library/LaunchDaemons/com.bluecontrol.watchdog.plist"

# ── LOGO (mesmo do Windows) ───────────────────────────────────────────────────
_LOGO_B64 = "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"

# ── HELPERS ───────────────────────────────────────────────────────────────────
def _sh(cmd: str, timeout: int = 30) -> tuple[bool, str]:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode == 0, (r.stdout + r.stderr).strip()

def _run_as_admin(script_content: str, timeout: int = 180) -> tuple[bool, str]:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False, prefix="/tmp/bc_")
    tmp.write("#!/bin/bash\n")
    tmp.write(script_content)
    tmp.close()
    os.chmod(tmp.name, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
    r = subprocess.run(
        ["osascript", "-e", f'do shell script "bash {tmp.name}" with administrator privileges'],
        capture_output=True, text=True, timeout=timeout
    )
    try:
        os.unlink(tmp.name)
    except Exception:
        pass
    return r.returncode == 0, (r.stdout + r.stderr).strip()

def _hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()

def _capturar_mac_gateway() -> str:
    try:
        ok, gw = _sh("route -n get default 2>/dev/null | grep gateway | awk '{print $2}'")
        if not ok or not gw:
            return ""
        _sh(f"ping -c 1 -W 1 {gw} > /dev/null 2>&1", timeout=5)
        ok, mac = _sh(f"arp -n {gw} 2>/dev/null | awk 'NR>1{{print $4}}' | head -1")
        if ok and mac and mac not in ("(incomplete)", "(unknown)"):
            return mac.upper().strip()
    except Exception:
        pass
    return ""

def _tmp(name: str, content: str) -> str:
    path = f"/tmp/bc_{name}"
    with open(path, "w") as f:
        f.write(content)
    return path

# ── GERAÇÃO DO SCRIPT DE INSTALAÇÃO ──────────────────────────────────────────


import urllib.request, urllib.parse, urllib.error


def gerar_script(patrimonio: str, api_url: str, token: str, intervalo_min: int,
                 habilitar_ssh: bool, exe_metricas: str) -> str:
    """Script (roda como root via osascript): grava config.json, instala o
    agente de métricas como LaunchDaemon e, opcionalmente, habilita o SSH.
    Sem VPN, sem senha de TI, sem mexer em usuários/admin."""
    api_url = api_url.rstrip("/")
    config_json = json.dumps({
        "patrimonio": patrimonio,
        "tipo_os": "macos",
        "agent_secret": token,
        "api_urls": [api_url],
        "intervalo_metricas_min": intervalo_min,
        "intervalo_inventario_min": 60,
    }, indent=2, ensure_ascii=False)

    plist_mtr = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.bluecontrol.metricas</string>
  <key>ProgramArguments</key><array><string>{BASE_DIR}/BlueControl_Metricas_Mac</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{BASE_DIR}/metricas_out.log</string>
  <key>StandardErrorPath</key><string>{BASE_DIR}/metricas_err.log</string>
  <key>EnvironmentVariables</key><dict><key>HOME</key><string>/var/root</string></dict>
</dict></plist>"""

    p_config = _tmp("config_json", config_json)
    p_plist  = _tmp("plist_mtr", plist_mtr)

    ssh_bloco = ""
    if habilitar_ssh:
        # Habilita SSH e prepara o usuário de serviço + chave, para o servidor
        # rodar comandos (desligar/reiniciar/bloquear/pop-up) sem senha.
        ssh_bloco = ("""
# ── Acesso remoto por SSH (usuário dedicado + chave) ──
systemsetup -setremotelogin on 2>/dev/null || true
launchctl load -w /System/Library/LaunchDaemons/ssh.plist 2>/dev/null || true

SVC="__USER__"
if ! id "$SVC" >/dev/null 2>&1; then
  RP="$(openssl rand -base64 24)"
  sysadminctl -addUser "$SVC" -fullName "BlueControl Servico" -password "$RP" -admin 2>/dev/null \\
    || sysadminctl -addUser "$SVC" -fullName "BlueControl Servico" -password "$RP" 2>/dev/null || true
fi
# Esconde da tela de login e garante grupos admin + acesso SSH
dscl . create /Users/"$SVC" IsHidden 1 2>/dev/null || true
dseditgroup -o edit -a "$SVC" -t user admin 2>/dev/null || true
dseditgroup -o edit -a "$SVC" -t user com.apple.access_ssh 2>/dev/null || true
# Chave pública autorizada
HOME_DIR="$(dscl . -read /Users/"$SVC" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
[ -z "$HOME_DIR" ] && HOME_DIR="/Users/$SVC"
mkdir -p "$HOME_DIR/.ssh"
printf '%s\\n' "__PUBKEY__" > "$HOME_DIR/.ssh/authorized_keys"
chown -R "$SVC" "$HOME_DIR/.ssh"
chmod 700 "$HOME_DIR/.ssh"; chmod 600 "$HOME_DIR/.ssh/authorized_keys"
# sudo sem senha (desligar/reiniciar/bloquear/pop-up precisam de root)
echo "$SVC ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/bluecontrol
chmod 440 /etc/sudoers.d/bluecontrol
echo "SSH_USER_OK"
""").replace("__USER__", SSH_USER).replace("__PUBKEY__", SSH_PUBKEY)

    return f"""#!/bin/bash
set -e
mkdir -p '{BASE_DIR}'

# Agente de métricas (LaunchDaemon)
cp '{exe_metricas}' '{BASE_DIR}/BlueControl_Metricas_Mac'
chmod +x '{BASE_DIR}/BlueControl_Metricas_Mac'
cp '{p_plist}' '{PLIST_MTR}'
chown root:wheel '{PLIST_MTR}'; chmod 644 '{PLIST_MTR}'
launchctl unload '{PLIST_MTR}' 2>/dev/null || true
launchctl load -w '{PLIST_MTR}'

# Configuração
cp '{p_config}' '{BASE_DIR}/config.json'
chown root:wheel '{BASE_DIR}/config.json'; chmod 600 '{BASE_DIR}/config.json'
{ssh_bloco}
echo "INSTALACAO_OK"
"""


_HTML = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><style>
:root{--bg:#0c0e10;--card:#161819;--bdr:#272a2c;--inp:#202224;--txt:#e2e4e6;--muted:#8a9099;--blue:#2563eb;--blueh:#1d4ed8;--green:#16a34a;--red:#dc2626}
*{box-sizing:border-box;font-family:-apple-system,system-ui,sans-serif}
body{margin:0;background:var(--bg);color:var(--txt);font-size:13.5px;padding:16px}
h1{font-size:16px;margin:6px 0 2px;display:flex;align-items:center;gap:8px}
.sub{color:var(--muted);font-size:12px;margin:0 0 14px}
img.logo{width:26px;height:26px;border-radius:6px}
.card{background:var(--card);border:1px solid var(--bdr);border-radius:10px;padding:12px;margin-bottom:12px}
label{display:block;font-size:11.5px;color:var(--muted);margin:8px 0 3px}
input,select{width:100%;padding:8px 9px;background:var(--inp);border:1px solid var(--bdr);border-radius:7px;color:var(--txt);font-size:13px;outline:none}
input:focus,select:focus{border-color:var(--blue)}
.tabs{display:flex;gap:6px;margin-bottom:10px}
.tab{flex:1;text-align:center;padding:8px;background:var(--inp);border:1px solid var(--bdr);border-radius:7px;cursor:pointer;font-size:12.5px;color:var(--muted)}
.tab.active{background:var(--blue);color:#fff;border-color:var(--blue)}
.pane{display:none}.pane.active{display:block}
.res-item{padding:8px 9px;border:1px solid var(--bdr);border-radius:7px;margin-top:6px;cursor:pointer}
.res-item:hover{border-color:var(--blue)}.res-item.sel{border-color:var(--green);background:#0f2018}
.res-nome{font-size:13px}.res-meta{font-size:11px;color:var(--muted)}
.banner{background:#0f2018;border:1px solid var(--green);border-radius:7px;padding:8px;margin-top:8px;font-size:12px;display:none}
.adv summary{cursor:pointer;color:var(--muted);font-size:12px}
button.go{width:100%;padding:11px;background:var(--blue);border:none;border-radius:8px;color:#fff;font-size:14px;font-weight:600;cursor:pointer;margin-top:4px}
button.go:hover{background:var(--blueh)}button.go:disabled{opacity:.5;cursor:default}
button.go.ok{background:var(--green)}button.go.err{background:var(--red)}
.bar{height:6px;background:var(--inp);border-radius:99px;margin-top:12px;overflow:hidden;display:none}
.bar>div{height:100%;width:0;background:var(--blue);transition:width .3s}
.plabel{font-size:11.5px;color:var(--muted);margin-top:6px;min-height:14px}
.log{background:#0a0c0d;border:1px solid var(--bdr);border-radius:7px;padding:8px;margin-top:8px;font-family:ui-monospace,Menlo,monospace;font-size:11px;max-height:150px;overflow:auto;display:none;white-space:pre-wrap}
.log .e{color:#f87171}.log .o{color:#4ade80}
</style></head><body>
<h1><img class="logo" src="data:image/x-icon;base64,__LOGO__">BlueControl IT — Instalador Mac</h1>
<p class="sub">Instala o monitoramento e habilita o acesso remoto (SSH).</p>

<div class="tabs">
  <div class="tab active" id="tabB" onclick="mudarTab('b')">Já cadastrada</div>
  <div class="tab" id="tabC" onclick="mudarTab('c')">Cadastrar nova</div>
</div>

<div class="card">
  <div class="pane active" id="paneB">
    <label>Buscar por nome, patrimônio ou responsável</label>
    <input id="busca" placeholder="digite ao menos 2 letras..." oninput="onBusca()">
    <div id="resultados"></div>
    <div class="banner" id="banner"></div>
  </div>
  <div class="pane" id="paneC">
    <label>Nome da máquina *</label><input id="cadNome" placeholder="ex.: MacBook do João">
    <label>Responsável</label><input id="cadResp">
    <label>Locadora</label><input id="cadLoc">
    <label>Categoria</label>
    <select id="cadCat"><option value="Notebooks">Notebook</option><option value="Desktop">Desktop</option><option value="Servidor">Servidor</option></select>
    <label>Setor</label><input id="cadSetor">
    <label>Número de série</label><input id="cadSerie">
  </div>
</div>

<details class="adv card"><summary>Servidor (avançado)</summary>
  <label>URL da API</label><input id="urlApi" value="http://192.168.0.17:8550">
  <label>Token do agente</label><input id="token" value="" placeholder="token fornecido pelo administrador">
</details>

<button class="go" id="btn" onclick="instalar()">Salvar e Instalar</button>
<div class="bar" id="bar"><div id="barIn"></div></div>
<div class="plabel" id="plabel"></div>
<div class="log" id="log"></div>

<script>
let aba='b', selec=null, buscaTimer=null;
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function mudarTab(t){aba=t;selec=null;
  tabB.classList.toggle('active',t==='b');tabC.classList.toggle('active',t==='c');
  paneB.classList.toggle('active',t==='b');paneC.classList.toggle('active',t==='c');
  banner.style.display='none';}
function onBusca(){clearTimeout(buscaTimer);const q=busca.value.trim();
  banner.style.display='none';selec=null;
  if(q.length<2){resultados.innerHTML='';return;}
  buscaTimer=setTimeout(()=>buscar(q),350);}
async function buscar(q){resultados.innerHTML='<div class="res-meta">Buscando…</div>';
  try{const r=await window.pywebview.api.buscar({url_api:urlApi.value.trim(),token:token.value.trim(),q});
    if(r.status!==200){resultados.innerHTML='<div class="res-meta">Erro: '+esc((r.data&&r.data.detail)||'falha')+'</div>';return;}
    const lst=(r.data&&r.data.dados)||[];
    if(!lst.length){resultados.innerHTML='<div class="res-meta">Nada encontrado.</div>';return;}
    window._res=lst;
    resultados.innerHTML=lst.map((m,i)=>'<div class="res-item" data-i="'+i+'" onclick="sel('+i+')"><div class="res-nome">'+esc(m.modelo||m.patrimonio_interno)+'</div><div class="res-meta">PAT '+esc(m.patrimonio_interno)+' · '+esc(m.responsavel||'sem responsável')+'</div></div>').join('');
  }catch(e){resultados.innerHTML='<div class="res-meta">Não foi possível contactar o servidor.</div>';}}
function sel(i){const m=window._res[i];selec=m.patrimonio_interno;
  document.querySelectorAll('.res-item').forEach(el=>el.classList.remove('sel'));
  document.querySelector('.res-item[data-i="'+i+'"]').classList.add('sel');
  banner.style.display='block';banner.textContent='Selecionada: '+(m.modelo||m.patrimonio_interno)+' (PAT '+m.patrimonio_interno+')';}
function log(m,c){log_.style.display='block';const s=document.createElement('span');if(c)s.className=c;s.textContent=m+'\\n';log_.appendChild(s);log_.scrollTop=log_.scrollHeight;}
window.log_=document.getElementById('log');
function setProgress(p){bar.style.display='block';barIn.style.width=p+'%';}
function setStatus(l,s){plabel.textContent=l+(s?' — '+s:'');}
function showComplete(){setProgress(100);setStatus('Instalação concluída!');btn.textContent='✔ Instalado';btn.className='go ok';}
function showError(m){btn.disabled=false;btn.textContent='Falhou — tentar de novo';btn.className='go err';log(m,'e');}
async function instalar(){
  const url=urlApi.value.trim(),tok=token.value.trim();
  if(!url||!tok){log('Preencha URL e token (em Servidor).','e');return;}
  btn.disabled=true;btn.className='go';btn.textContent='Instalando…';log_.innerHTML='';
  let pat=null;
  if(aba==='b'){ if(!selec){showError('Selecione uma máquina na lista.');return;} pat=selec; log('Usando PAT '+pat,'o'); }
  else{ const nome=cadNome.value.trim(); if(!nome){showError('Nome da máquina é obrigatório.');return;}
    log('Cadastrando máquina…');
    try{const r=await window.pywebview.api.cadastrar({url_api:url,token:tok,nome_maquina:nome,responsavel:cadResp.value.trim()||null,locadora:cadLoc.value.trim()||null,categoria:cadCat.value,setor:cadSetor.value.trim()||null,numero_serie:cadSerie.value.trim()||null});
      if(r.status!==200){showError('Erro ao cadastrar: '+((r.data&&r.data.detail)||'falha'));return;}
      pat=r.data.patrimonio;log('Cadastrada — PAT '+pat,'o');
    }catch(e){showError('Não foi possível contactar o servidor.');return;}}
  window.pywebview.api.instalar({patrimonio:pat,url_api:url,token:tok});
}
</script></body></html>"""


def _http_json(method, url, token, body=None, timeout=12):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Agent-Token", token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", str(e))
        except Exception:
            detail = str(e)
        return e.code, {"detail": detail}
    except Exception as e:
        return 0, {"detail": f"Não foi possível conectar em {url} ({e})"}


class _Api:
    def __init__(self):
        self._win = None

    def _js(self, code):
        if self._win:
            try:
                self._win.evaluate_js(code)
            except Exception:
                pass

    def buscar(self, params):
        url = params["url_api"].rstrip("/") + "/api/agente/equipamentos/buscar?q=" + urllib.parse.quote(params.get("q", ""))
        status, data = _http_json("GET", url, params["token"])
        return {"status": status, "data": data}

    def cadastrar(self, params):
        url = params["url_api"].rstrip("/") + "/api/agente/equipamentos"
        body = {k: params.get(k) for k in ("nome_maquina", "responsavel", "locadora", "categoria", "setor", "numero_serie")}
        body["tipo_os"] = "macos"
        status, data = _http_json("POST", url, params["token"], body)
        return {"status": status, "data": data}

    def instalar(self, params):
        threading.Thread(target=self._run, args=(params,), daemon=True).start()
        return True

    def _run(self, patrimonio_ou_params, *_):
        import json as _j
        p = patrimonio_ou_params
        patrimonio, url_api, token = p["patrimonio"], p["url_api"], p["token"]
        try:
            self._js("setStatus('Localizando o agente de métricas','')"); self._js("setProgress(10)")
            script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            candidatos = [
                os.path.join(getattr(sys, "_MEIPASS", script_dir), "BlueControl_Metricas_Mac"),
                os.path.join(script_dir, "BlueControl_Metricas_Mac"),
                os.path.join(os.getcwd(), "BlueControl_Metricas_Mac"),
            ]
            exe = next((c for c in candidatos if os.path.exists(c)), "")
            if not exe:
                raise RuntimeError("BlueControl_Metricas_Mac não encontrado no pacote")

            self._js("setStatus('Gerando script de instalação','')"); self._js("setProgress(25)")
            script = gerar_script(patrimonio.strip().upper(), url_api, token, 5, True, exe)

            self._js("setStatus('Solicitando privilégios de administrador','Aguarde o diálogo de senha do macOS')"); self._js("setProgress(45)")
            ok, out = _run_as_admin(script, timeout=240)
            self._js("setProgress(85)")
            if not ok or "INSTALACAO_OK" not in out:
                raise RuntimeError(out[:300] or "Script retornou falha")

            self._js("setStatus('Concluindo','')"); self._js("setProgress(97)")
            self._js("showComplete()")
        except Exception as e:
            self._js(f"showError({_j.dumps(str(e))})")


def _setup_mac_edit_menu():
    """Menu Editar nativo, para Cmd+C/V/X funcionarem no pywebview."""
    import time; time.sleep(0.4)
    try:
        from AppKit import NSApplication, NSMenu, NSMenuItem
        import objc

        class _Helper(objc.NSObject):
            def addMenu_(self, _):
                app = NSApplication.sharedApplication()
                mb = app.mainMenu()
                if not mb:
                    return
                for i in range(mb.numberOfItems()):
                    if mb.itemAtIndex_(i).title() in ("Editar", "Edit"):
                        return
                ei = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Editar", None, "")
                mb.addItem_(ei)
                em = NSMenu.alloc().initWithTitle_("Editar")
                ei.setSubmenu_(em)
                for t, a, k in [("Recortar", "cut:", "x"), ("Copiar", "copy:", "c"),
                                ("Colar", "paste:", "v"), ("Selecionar Tudo", "selectAll:", "a")]:
                    em.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(t, a, k))
        h = _Helper.new()
        h.performSelectorOnMainThread_withObject_waitUntilDone_("addMenu:", None, False)
    except Exception as e:
        print(f"[mac menu] {e}")


if __name__ == "__main__":
    api = _Api()
    win = webview.create_window(
        "BlueControl IT — Instalador Mac",
        html=_HTML.replace("__LOGO__", _LOGO_B64),
        js_api=api,
        width=500,
        height=780,
        min_size=(460, 640),
        background_color="#0c0e10",
        text_select=False,
    )
    api._win = win
    webview.start(func=_setup_mac_edit_menu)
