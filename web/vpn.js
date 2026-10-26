// ============================================
//  Agente Blue — vpn.js  (aba VPN)
//
//  Todo global daqui começa com "vpn"/"_vpn": os scripts das abas dividem o
//  mesmo escopo da página, e um nome repetido (let declarado duas vezes) é
//  SyntaxError que derruba a interface inteira, não só esta aba.
// ============================================

let _vpnEventSource = null;
let _vpnInstalando = false;
let _vpnRemovendo = false;   // separa o progresso da remocao do da instalacao

function _vpnEsc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Caixa própria, que ACUMULA as linhas. Antes isto escrevia no
// vpnProgressLabel — o mesmo elemento da barra de progresso —, então a
// mensagem com a causa do erro era sobrescrita pelo texto final na mesma
// hora e nunca dava para saber o que tinha falhado.
function vpnLog(msg, tipo) {
  const box = document.getElementById('vpnLogBox');
  if (!box) return;
  box.classList.add('show');
  const linha = document.createElement('span');
  linha.className = 'log-line ' + (tipo || 'muted');
  linha.textContent = msg;
  box.appendChild(linha);
  box.scrollTop = box.scrollHeight;
}

function vpnSetProgress(pct, texto, ok, erro) {
  const barra = document.getElementById('vpnProgressBar');
  const rotulo = document.getElementById('vpnProgressLabel');
  if (barra) barra.style.width = Math.max(0, Math.min(100, pct)) + '%';
  if (rotulo) rotulo.textContent = texto || '';
  if (barra) {
    barra.classList.toggle('ok', !!ok);
    barra.classList.toggle('erro', !!erro);
  }
}

async function vpnCarregarStatus() {
  try {
    const d = await apiGet('/api/vpn/status');
    const dot = document.getElementById('vpnStatusDot');
    const titulo = document.getElementById('vpnStatusTitulo');
    const detalhe = document.getElementById('vpnStatusDetalhe');

    if (!d.instalado) {
      titulo.textContent = 'VPN não instalada';
      detalhe.textContent = d.instalador
        ? `Instalador encontrado: ${d.instalador}`
        : 'Instalador NÃO encontrado — coloque o openvpn-*.exe em installers\\';
      dot.className = 'status-dot' + (d.instalador ? '' : ' erro');
      return;
    }

    // Credencial gravada é o que diferencia "instalado" de "pronto para
    // conectar": sem ela o OpenVPN pede senha na tela a cada conexão, que é
    // exatamente o que esta aba existe para evitar.
    const comCred = (d.com_credencial || []).length;
    const total = (d.perfis || []).length;
    if (comCred > 0) {
      // Conexao automatica exige as DUAS coisas: perfil em config-auto e o
      // OpenVPNService rodando. Mostrar as duas evita a conclusao errada de
      // que esta automatico quando o servico esta parado.
      const auto = d.autoconexao && d.servico === 'rodando';
      titulo.textContent = auto ? 'VPN pronta (conexão automática)' : 'VPN pronta';
      detalhe.textContent = `${comCred} de ${total} perfil(is) com credencial `
        + `gravada — ${_vpnEsc((d.perfis || []).join(', '))}`
        + (auto ? ' · conecta sozinha no boot'
                : d.autoconexao ? ' · perfil em config-auto, mas o '
                                  + `OpenVPNService está ${_vpnEsc(d.servico)}`
                                : ' · conexão manual pelo OpenVPN GUI');
      dot.className = 'status-dot ok';
    } else if (total > 0) {
      titulo.textContent = 'VPN instalada, sem credencial';
      detalhe.textContent = `Perfil ${_vpnEsc(d.perfis.join(', '))} — vai pedir `
        + 'usuário e senha a cada conexão';
      dot.className = 'status-dot';
    } else {
      titulo.textContent = 'OpenVPN instalado, sem perfil';
      detalhe.textContent = 'Nenhum .ovpn encontrado em ' + _vpnEsc(d.pasta);
      dot.className = 'status-dot erro';
    }
  } catch (e) {
    vpnLog('Não consegui ler o estado da VPN.', 'err');
  }
}

function _vpnHandleEvent(event) {
  let e;
  try { e = JSON.parse(event.data); } catch (_) { return; }
  try {
    if (e.type === 'vpn_log') {
      vpnLog(e.msg, e.tipo);
    } else if (e.type === 'vpn_progresso') {
      vpnSetProgress(e.pct, e.texto, e.ok, e.erro);
      if (e.pct >= 100) {
        // Instalar e remover compartilham o canal vpn_progresso. Sem separar,
        // o fim de uma remoção escreveria "✔ VPN PRONTA" no botão de instalar
        // e deixaria o de remover travado em "REMOVENDO…" — foi o que
        // aconteceu na aba Monitoramento.
        if (_vpnRemovendo) {
          const btnR = document.getElementById('vpnBtnRemover');
          btnR.disabled = false;
          btnR.textContent = e.erro ? 'FALHOU — TENTAR DE NOVO'
                                    : 'Remover a VPN e limpar tudo';
          _vpnRemovendo = false;
        } else {
          const btn = document.getElementById('vpnBtnInstalar');
          btn.disabled = false;
          btn.textContent = e.ok ? '✔  VPN PRONTA'
                          : e.erro ? 'FALHOU — TENTAR DE NOVO'
                          : 'INSTALAR VPN';
          btn.classList.toggle('success', !!e.ok);
          btn.classList.toggle('erro', !!e.erro);
        }
        _vpnInstalando = false;
        if (_vpnEventSource) { _vpnEventSource.close(); _vpnEventSource = null; }
        if (e.ok) setTimeout(vpnCarregarStatus, 1000);
      }
    }
  } catch (_) {
    // um evento problemático não interrompe os próximos
  }
}

async function vpnInstalar() {
  if (_vpnInstalando) return;
  const usuario = document.getElementById('vpnUsuario').value.trim();
  const senha = document.getElementById('vpnSenha').value;

  if (!usuario || !senha) {
    vpnLog('Preencha usuário e senha da VPN deste colaborador.', 'err');
    return;
  }

  const btn = document.getElementById('vpnBtnInstalar');
  btn.disabled = true;
  btn.textContent = 'INSTALANDO…';
  btn.classList.remove('success', 'erro');
  _vpnInstalando = true;

  if (!_vpnEventSource) {
    _vpnEventSource = apiStream('/api/stream');
    _vpnEventSource.onmessage = _vpnHandleEvent;
    _vpnEventSource.onerror = () => vpnLog('Conexão com o agente local perdida.', 'warn');
  }

  try {
    await apiPost('/api/vpn/instalar', { usuario, senha });
    // A senha some do campo assim que sai daqui: a janela costuma ficar
    // aberta enquanto o técnico passa para a próxima máquina.
    document.getElementById('vpnSenha').value = '';
  } catch (e) {
    vpnLog('Falha ao contactar o agente local.', 'err');
    btn.disabled = false;
    btn.textContent = 'INSTALAR VPN';
    _vpnInstalando = false;
  }
}


// ── Remover a VPN ─────────────────────────────────────────────────────────
// Confirmação simples, não por digitação como a exclusão de máquina: aqui
// nada é apagado no servidor e refazer é só reinstalar. O que se perde é a
// credencial do colaborador, que ele mesmo sabe informar de novo.
async function vpnRemover() {
  if (_vpnInstalando) return;
  if (!confirm('Remover a VPN desta máquina?\n\n'
             + 'Desinstala o OpenVPN e apaga os perfis e as credenciais '
             + 'gravadas. Depois é só instalar de novo pela aba.')) return;

  const btn = document.getElementById('vpnBtnRemover');
  btn.disabled = true;
  btn.textContent = 'REMOVENDO…';
  _vpnInstalando = true;   // bloqueia instalar e remover ao mesmo tempo
  _vpnRemovendo = true;

  if (!_vpnEventSource) {
    _vpnEventSource = apiStream('/api/stream');
    _vpnEventSource.onmessage = _vpnHandleEvent;
    _vpnEventSource.onerror = () => vpnLog('Conexão com o agente local perdida.', 'warn');
  }

  try {
    await apiPost('/api/vpn/limpar', {});
  } catch (e) {
    vpnLog('Falha ao contactar o agente local.', 'err');
    btn.disabled = false;
    btn.textContent = 'Remover a VPN e limpar tudo';
    _vpnInstalando = false;
    _vpnRemovendo = false;
  }
}
