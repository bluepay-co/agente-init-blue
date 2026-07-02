// ============================================
//  Agente Blue — programas.js  (v3.0)
//  Aba "Desinstalar Programas": listar, filtrar,
//  selecionar e desinstalar em lote (com SSE).
// ============================================

let _programas = [];        // catálogo da última listagem (na ordem exibida)
let _progES = null;         // EventSource ativo durante a desinstalação
let _barPorNome = {};       // nome do programa → elementos de progresso

// ── Listar ───────────────────────────────────────────────────────────────────
async function carregarProgramas() {
  const lista = document.getElementById('prog-list');
  lista.innerHTML = '<p class="u-loading">Carregando… (pode levar alguns segundos)</p>';
  document.getElementById('prog-all').checked = false;

  let data;
  try {
    const r = await fetch('/api/programas/listar');
    data = await r.json();
  } catch (e) {
    lista.innerHTML = '<p class="u-loading">Falha ao carregar programas.</p>';
    return;
  }
  if (!data.ok) {
    lista.innerHTML = '<p class="u-loading">' + _escProg(data.erro || 'Erro ao listar.') + '</p>';
    return;
  }

  _programas = data.programas || [];
  if (_programas.length === 0) {
    lista.innerHTML = '<p class="u-loading">Nenhum programa encontrado.</p>';
    return;
  }

  lista.innerHTML = _programas.map(renderPrograma).join('');
  atualizarContagem();
}

function renderPrograma(p, i) {
  const nome = _escProg(p.nome);
  const ver = p.versao ? _escProg(p.versao) : '';
  const pub = p.publisher ? _escProg(p.publisher) : '';
  const badge = p.tipo === 'store'
    ? '<span class="u-badge">Store</span>'
    : '<span class="u-badge admin">Desktop</span>';
  const meta = [ver ? 'v' + ver : '', pub].filter(Boolean).join(' · ');

  return `
  <div class="prog-item" id="prog-item-${i}" data-search="${_escProg((p.nome + ' ' + (p.publisher || '')).toLowerCase())}">
    <div class="prog-head">
      <label class="prog-check-wrap">
        <input type="checkbox" class="prog-cb" data-idx="${i}" onchange="atualizarContagem()">
        <span class="prog-name">${nome} ${badge}</span>
      </label>
      <span class="prog-status" id="prog-status-${i}"></span>
    </div>
    <div class="prog-meta">${meta}</div>
    <div class="prog-bar-wrap"><div class="prog-bar" id="prog-bar-${i}"></div></div>
  </div>`;
}

// ── Filtro / seleção ─────────────────────────────────────────────────────────
function filtrarProgramas() {
  const termo = document.getElementById('prog-search').value.trim().toLowerCase();
  document.querySelectorAll('.prog-item').forEach(item => {
    const hay = item.getAttribute('data-search') || '';
    item.style.display = (!termo || hay.indexOf(termo) !== -1) ? 'block' : 'none';
  });
}

function marcarTodos(marcar) {
  document.querySelectorAll('.prog-item').forEach(item => {
    if (item.style.display === 'none') return;         // só os visíveis (respeita o filtro)
    const cb = item.querySelector('.prog-cb');
    if (cb) cb.checked = marcar;
  });
  atualizarContagem();
}

function _selecionados() {
  return Array.from(document.querySelectorAll('.prog-cb:checked'))
    .map(cb => _programas[parseInt(cb.getAttribute('data-idx'), 10)])
    .filter(Boolean);
}

function atualizarContagem() {
  const n = document.querySelectorAll('.prog-cb:checked').length;
  document.getElementById('prog-count').textContent = n + ' selecionado' + (n === 1 ? '' : 's');
  document.getElementById('btnDesinstalar').disabled = (n === 0);
}

// ── Desinstalar em lote ──────────────────────────────────────────────────────
async function desinstalarSelecionados() {
  const sel = _selecionados();
  if (sel.length === 0) return;

  if (!confirm(`Desinstalar ${sel.length} programa(s)?\n\nO agente também vai remover as sobras (pastas e chave de desinstalação).`)) {
    return;
  }

  const btn = document.getElementById('btnDesinstalar');
  const msg = document.getElementById('prog-msg');
  btn.disabled = true;
  btn.textContent = '⏳  DESINSTALANDO…';
  _msgP(msg, 'Iniciando desinstalação…', 'info');

  // Mapeia nome → elementos de progresso (para atualizar a barra certa via SSE).
  _barPorNome = {};
  sel.forEach(p => {
    const idx = _programas.indexOf(p);
    _barPorNome[p.nome] = {
      bar:    document.getElementById('prog-bar-' + idx),
      status: document.getElementById('prog-status-' + idx),
      item:   document.getElementById('prog-item-' + idx),
    };
  });

  // Abre o stream ANTES de disparar, para não perder os primeiros eventos.
  if (_progES) { _progES.close(); _progES = null; }
  _progES = new EventSource('/api/stream');
  _progES.onmessage = _handleProgEvent;
  _progES.onopen = () => {
    fetch('/api/programas/desinstalar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: sel.map(p => p.id) })
    }).catch(() => _msgP(msg, 'Falha ao contactar o servidor.', 'err'));
  };
}

function _handleProgEvent(ev) {
  let e;
  try { e = JSON.parse(ev.data); } catch (_) { return; }

  if (e.type === 'prog_uninstall') {
    _updateProgBar(e.nome, e.estado, e.pct || 0);
  } else if (e.type === 'prog_fim') {
    _onProgFim(e);
  }
}

function _updateProgBar(nome, estado, pct) {
  const el = _barPorNome[nome];
  if (!el || !el.bar) return;
  const mapa = {
    desinstalando: { cls: 'downloading', txt: 'Desinstalando…', w: Math.max(pct, 10) },
    limpando:      { cls: 'installing',  txt: 'Limpando sobras…', w: 70 },
    ok:            { cls: 'done',        txt: '✔ Concluído',    w: 100 },
    erro:          { cls: 'error',       txt: '✕ Erro',         w: 100 },
    manual:        { cls: 'error',       txt: '⚠ Requer manual', w: 100 },
  };
  const m = mapa[estado] || mapa.desinstalando;
  el.bar.className = 'prog-bar ' + m.cls;
  el.bar.style.width = m.w + '%';
  if (el.status) { el.status.textContent = m.txt; el.status.className = 'prog-status ' + m.cls; }
  if (el.item && estado === 'ok') el.item.classList.add('finished');
}

function _onProgFim(e) {
  if (_progES) { _progES.close(); _progES = null; }
  const btn = document.getElementById('btnDesinstalar');
  const msg = document.getElementById('prog-msg');
  btn.textContent = '🗑  DESINSTALAR SELECIONADOS';
  btn.disabled = false;

  const tipo = e.erro > 0 ? 'err' : 'ok';
  _msgP(msg, `Concluído: ${e.ok} desinstalado(s), ${e.erro} com falha. Atualizando lista…`, tipo);
  setTimeout(() => carregarProgramas(), 1500);
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function _msgP(el, texto, tipo) {
  el.textContent = texto || '';
  el.className = 'u-msg' + (tipo ? ' ' + tipo : '');
}

function _escProg(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
