// ============================================
//  Agente Blue — usuarios.js  (v3.0)
//  Painel "Gerenciar Usuários": listar, criar,
//  editar (renomear + senha) e excluir.
//  JavaScript puro, sem dependências.
// ============================================

// ── Navegação entre as views (Setup / Usuários / Programas / Monitoramento) ──
function showView(view) {
  const views = { main: 'viewMain', users: 'viewUsers',
                  programs: 'viewPrograms', monitor: 'viewMonitor' };
  const tabs  = { main: 'tab-setup', users: 'tab-users',
                  programs: 'tab-programs', monitor: 'tab-monitor' };

  Object.keys(views).forEach(k => {
    const el = document.getElementById(views[k]);
    if (el) el.style.display = (k === view) ? 'flex' : 'none';
    const t = document.getElementById(tabs[k]);
    if (t) t.classList.toggle('active', k === view);
  });

  if (view === 'users') { carregarUsuarios(); carregarHostname(); }
  if (view === 'programs' && typeof carregarProgramas === 'function') carregarProgramas();
  if (view === 'monitor' && typeof monCarregarStatus === 'function') monCarregarStatus();
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function _msg(el, texto, tipo) {
  el.textContent = texto || '';
  el.className = 'u-msg' + (tipo ? ' ' + tipo : '');
}

async function _post(url, body) {
  try {
    return await apiPost(url, body);
  } catch (e) {
    return { ok: false, erro: 'Falha ao contactar o servidor.' };
  }
}

function _escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Listar ───────────────────────────────────────────────────────────────────
async function carregarUsuarios() {
  const lista = document.getElementById('user-list');
  lista.innerHTML = '<p class="u-loading">Carregando…</p>';

  let data;
  try {
    const r = await apiFetch('/api/usuarios/listar');
    data = await r.json();
  } catch (e) {
    lista.innerHTML = '<p class="u-loading">Falha ao carregar usuários.</p>';
    return;
  }

  if (!data.ok) {
    lista.innerHTML = '<p class="u-loading">' + _escapeHtml(data.erro || 'Erro ao listar.') + '</p>';
    return;
  }
  if (!data.usuarios || data.usuarios.length === 0) {
    lista.innerHTML = '<p class="u-loading">Nenhum usuário encontrado.</p>';
    return;
  }

  // Mapa sid → usuário: os handlers recebem só o sid (que nunca tem aspas/HTML),
  // e buscam o nome aqui. Evita quebrar o onclick com nomes tipo "O'Brien".
  _usuarios = {};
  data.usuarios.forEach(u => { _usuarios[u.sid] = u; });

  lista.innerHTML = data.usuarios.map(renderUsuario).join('');
}

// Cache dos usuários da última listagem, indexado por SID.
let _usuarios = {};

function renderUsuario(u) {
  const sid = _escapeHtml(u.sid);
  const nome = _escapeHtml(u.nome);
  const pasta = u.profile ? _escapeHtml(u.profile) : '— (sem perfil; criado no 1º login)';
  const cargo = u.admin
    ? '<span class="u-badge admin">Administrador</span>'
    : '<span class="u-badge">Usuário</span>';
  const flags = [];
  if (u.builtin) flags.push('<span class="u-badge lock">Interno</span>');
  if (u.logado)  flags.push('<span class="u-badge on">Logado</span>');
  if (u.enabled === false) flags.push('<span class="u-badge off">Desativado</span>');

  return `
  <div class="u-item" id="u-${sid}">
    <div class="u-row">
      <div class="u-main">
        <div class="u-name">${nome} ${cargo} ${flags.join(' ')}</div>
        <div class="u-path">${pasta}</div>
      </div>
      <div class="u-actions">
        <button class="btn-user" onclick="abrirEditar('${sid}')">Editar</button>
        <button class="btn-user danger" onclick="abrirExcluir('${sid}')">Excluir</button>
      </div>
    </div>
    <div class="u-panel" id="panel-${sid}"></div>
  </div>`;
}

// ── Criar ────────────────────────────────────────────────────────────────────
async function criarUsuario() {
  const nomeEl  = document.getElementById('nu-nome');
  const senhaEl = document.getElementById('nu-senha');
  const adminEl = document.getElementById('nu-admin');
  const msg     = document.getElementById('nu-msg');

  const nome = nomeEl.value.trim();
  if (!nome) { _msg(msg, 'Informe um nome de usuário.', 'err'); return; }

  _msg(msg, 'Criando…', 'info');
  const r = await _post('/api/usuarios/criar', {
    nome: nome, senha: senhaEl.value, admin: adminEl.checked
  });

  if (r.ok) {
    _msg(msg, r.msg || 'Usuário criado.', 'ok');
    nomeEl.value = ''; senhaEl.value = ''; adminEl.checked = false;
    carregarUsuarios();
  } else {
    _msg(msg, r.erro || 'Falha ao criar.', 'err');
  }
}

// ── Editar (renomear + senha) ────────────────────────────────────────────────
function abrirEditar(sid) {
  const panel = document.getElementById('panel-' + sid);
  if (panel.dataset.open === 'editar') { fecharPanel(sid); return; }

  const nome = (_usuarios[sid] || {}).nome || '';
  panel.dataset.open = 'editar';
  panel.innerHTML = `
    <div class="u-edit">
      <p class="u-edit-title">Editar <strong>${_escapeHtml(nome)}</strong></p>
      <input class="u-input" id="ed-nome-${sid}" placeholder="Novo nome (deixe vazio p/ manter)" maxlength="20" value="${_escapeHtml(nome)}" autocomplete="off">
      <input class="u-input" id="ed-senha-${sid}" type="password" placeholder="Nova senha (deixe vazio p/ manter)" autocomplete="new-password">
      <div class="u-edit-actions">
        <button class="btn-user primary" onclick="salvarEditar('${sid}')">Salvar</button>
        <button class="btn-user" onclick="fecharPanel('${sid}')">Cancelar</button>
      </div>
      <p class="u-msg" id="ed-msg-${sid}"></p>
    </div>`;
}

async function salvarEditar(sid) {
  const nomeAtual = (_usuarios[sid] || {}).nome || '';
  const novoNome = document.getElementById('ed-nome-' + sid).value.trim();
  const novaSenha = document.getElementById('ed-senha-' + sid).value;
  const msg = document.getElementById('ed-msg-' + sid);

  // Se o nome não mudou, não envia rename (evita erro "nome igual ao atual").
  const nomeParaEnviar = (novoNome && novoNome !== nomeAtual) ? novoNome : '';
  if (!nomeParaEnviar && !novaSenha) {
    _msg(msg, 'Altere o nome ou a senha.', 'err');
    return;
  }

  _msg(msg, 'Salvando…', 'info');
  const r = await _post('/api/usuarios/editar', {
    sid: sid, novo_nome: nomeParaEnviar, nova_senha: novaSenha
  });

  if (r.ok) {
    if (r.modo === 'reboot') {
      _msg(msg, r.msg || 'Renomeação agendada; o PC vai reiniciar.', 'info');
      alert(r.msg);
    } else {
      _msg(msg, r.msg || 'Alterações salvas.', 'ok');
      setTimeout(() => carregarUsuarios(), 700);
    }
  } else {
    _msg(msg, r.erro || 'Falha ao salvar.', 'err');
  }
}

// ── Excluir ──────────────────────────────────────────────────────────────────
function abrirExcluir(sid) {
  const panel = document.getElementById('panel-' + sid);
  if (panel.dataset.open === 'excluir') { fecharPanel(sid); return; }

  const nome = (_usuarios[sid] || {}).nome || '';
  panel.dataset.open = 'excluir';
  panel.innerHTML = `
    <div class="u-del">
      <p class="u-del-title">Excluir <strong>${_escapeHtml(nome)}</strong>?</p>
      <label class="u-role">
        <input type="checkbox" id="del-perfil-${sid}">
        <span>Apagar também a pasta e os dados do perfil (irreversível)</span>
      </label>
      <div class="u-edit-actions">
        <button class="btn-user danger" onclick="confirmarExcluir('${sid}')">Confirmar exclusão</button>
        <button class="btn-user" onclick="fecharPanel('${sid}')">Cancelar</button>
      </div>
      <p class="u-msg" id="del-msg-${sid}"></p>
    </div>`;
}

async function confirmarExcluir(sid) {
  const apagar = document.getElementById('del-perfil-' + sid).checked;
  const msg = document.getElementById('del-msg-' + sid);

  _msg(msg, 'Excluindo…', 'info');
  const r = await _post('/api/usuarios/excluir', { sid: sid, apagar_perfil: apagar });

  if (r.ok) {
    _msg(msg, r.msg || 'Usuário excluído.', 'ok');
    setTimeout(() => carregarUsuarios(), 700);
  } else {
    _msg(msg, r.erro || 'Falha ao excluir.', 'err');
  }
}

// ── Fechar painel inline ─────────────────────────────────────────────────────
function fecharPanel(sid) {
  const panel = document.getElementById('panel-' + sid);
  if (panel) { panel.dataset.open = ''; panel.innerHTML = ''; }
}

// ── Nome da máquina ──────────────────────────────────────────────────────────
// É por este nome que o equipamento aparece no pfSense (lease de DHCP).

async function carregarHostname() {
  const elAtual = document.getElementById('host-atual');
  const elPend  = document.getElementById('host-pendente');
  try {
    const d = await apiGet('/api/usuarios/hostname');
    if (!d.ok) { elAtual.textContent = '—'; return; }

    elAtual.textContent = d.atual || '—';
    // Campo começa com o nome atual: quase sempre o técnico ajusta o que já
    // existe em vez de digitar do zero.
    const campo = document.getElementById('host-novo');
    if (!campo.value) campo.value = d.atual || '';

    if (d.pendente) {
      elPend.style.display = 'block';
      elPend.textContent = `Reinício pendente: vira "${d.pendente}" no próximo boot.`;
    } else {
      elPend.style.display = 'none';
    }
  } catch (e) {
    elAtual.textContent = '—';
  }
}

async function renomearMaquina() {
  const campo = document.getElementById('host-novo');
  const msg   = document.getElementById('host-msg');
  const novo  = campo.value.trim();

  if (!novo) { _msg(msg, 'Informe o novo nome da máquina.', 'err'); return; }

  const btn = msg.parentElement.querySelector('.btn-user.primary');
  if (btn) { btn.disabled = true; btn.textContent = 'Renomeando…'; }
  _msg(msg, 'Renomeando…', '');

  const d = await _post('/api/usuarios/hostname', { novo_nome: novo });

  if (btn) { btn.disabled = false; btn.textContent = 'Renomear máquina'; }
  if (d.ok) {
    _msg(msg, d.msg || 'Nome alterado. Reinicie a máquina.', 'ok');
    carregarHostname();
  } else {
    _msg(msg, d.erro || 'Falha ao renomear a máquina.', 'err');
  }
}
