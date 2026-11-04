// ============================================
//  Configurador do Agente — script.js
//  SSE + fetch — puro JavaScript, sem dependências
// ============================================

let _eventSource = null;
let _maquinaSelecionada = null;   // {patrimonio_interno, modelo, responsavel, ...} — quando veio da busca
let _buscaTimer = null;

// ── Status atual ─────────────────────────────

async function carregarStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();

    // Os campos avançados vêm antes do early return: numa máquina nova
    // (existe = false) eles são a única fonte de URL e token para buscar e
    // cadastrar. O /api/status já devolve os padrões quando não há config.
    if (d.url_api)      document.getElementById('cfgUrlApi').value    = d.url_api;
    if (d.token_agente) document.getElementById('cfgToken').value     = d.token_agente;
    if (d.intervalo_segundos) document.getElementById('cfgIntervalo').value = String(d.intervalo_segundos);

    if (!d.existe) return;

    document.getElementById('statusCard').style.display = 'block';
    document.getElementById('statusPatrimonio').textContent = `Patrimônio ${d.patrimonio}`;
    const detalheMap = {
      rodando: 'Serviço rodando — ' + d.url_api,
      parado: 'Serviço parado — ' + d.url_api,
      nao_instalado: 'Configurado, serviço não instalado',
      desconhecido: d.url_api,
    };
    document.getElementById('statusDetalhe').textContent = detalheMap[d.servico] || d.url_api;
    const dot = document.getElementById('statusDot');
    dot.className = 'status-dot ' + (d.servico === 'rodando' ? 'on' : d.servico === 'parado' ? 'off' : 'none');
  } catch (e) {
    // Sem servidor local respondendo ainda — ignora, é só um card informativo
  }
}

// ── Tabs ──────────────────────────────────────

function mudarTab(tab) {
  document.getElementById('tabBtnBuscar').classList.toggle('active', tab === 'buscar');
  document.getElementById('tabBtnCadastrar').classList.toggle('active', tab === 'cadastrar');
  document.getElementById('tabBuscar').classList.toggle('active', tab === 'buscar');
  document.getElementById('tabCadastrar').classList.toggle('active', tab === 'cadastrar');
}

function toggleAvancado() {
  const el = document.getElementById('avancadoFields');
  const show = el.style.display === 'none';
  el.style.display = show ? 'flex' : 'none';
  document.getElementById('avancadoToggle').textContent = show ? 'ocultar' : 'mostrar';
}

// ── Busca de máquina existente ───────────────

function onBuscaInput() {
  clearTimeout(_buscaTimer);
  const q = document.getElementById('inpBusca').value.trim();
  document.getElementById('selecionadoBanner').classList.remove('show');
  _maquinaSelecionada = null;
  if (q.length < 2) {
    document.getElementById('resultados').innerHTML = '';
    return;
  }
  _buscaTimer = setTimeout(() => buscarMaquinas(q), 350);
}

async function buscarMaquinas(q) {
  const box = document.getElementById('resultados');
  const urlApi = document.getElementById('cfgUrlApi').value.trim();
  const token = document.getElementById('cfgToken').value.trim();
  // Sem token a busca volta 401 e o motivo nao aparece. Diz qual campo falta.
  if (!urlApi || !token) {
    box.innerHTML = '<div class="resultado-vazio">Preencha a URL da API e o token do agente em "Servidor (avançado)".</div>';
    return;
  }
  box.innerHTML = '<div class="resultado-vazio">Buscando…</div>';
  try {
    const r = await fetch('/api/buscar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url_api: urlApi, token_agente: token, q }),
    });
    const d = await r.json();
    if (!r.ok) {
      box.innerHTML = `<div class="resultado-vazio">Erro: ${d.detail || 'não foi possível buscar'}</div>`;
      return;
    }
    if (!d.dados || !d.dados.length) {
      box.innerHTML = '<div class="resultado-vazio">Nenhuma máquina encontrada com esse termo.</div>';
      return;
    }
    box.innerHTML = d.dados.map((m, i) => `
      <div class="resultado-item" data-idx="${i}" onclick="selecionarMaquina(${i})">
        <span class="resultado-nome">${escapeHtml(m.modelo || m.patrimonio_interno)}</span>
        <span class="resultado-meta">PAT ${escapeHtml(m.patrimonio_interno)} · ${escapeHtml(m.responsavel || 'sem responsável')}</span>
      </div>
    `).join('');
    window._resultadosBusca = d.dados;
  } catch (e) {
    box.innerHTML = '<div class="resultado-vazio">Não foi possível contactar o servidor BlueControl.</div>';
  }
}

function selecionarMaquina(idx) {
  const m = window._resultadosBusca[idx];
  _maquinaSelecionada = m;
  document.querySelectorAll('.resultado-item').forEach(el => el.classList.remove('selected'));
  document.querySelector(`.resultado-item[data-idx="${idx}"]`).classList.add('selected');
  document.getElementById('selecionadoBanner').classList.add('show');
  document.getElementById('selecionadoNome').textContent = `${m.modelo || m.patrimonio_interno} (PAT ${m.patrimonio_interno})`;
}

function escapeHtml(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Log / progresso ───────────────────────────

function appendLog(msg, tipo) {
  const box = document.getElementById('logBox');
  box.classList.add('show');
  const line = document.createElement('span');
  line.className = 'log-line ' + (tipo || 'muted');
  line.textContent = msg;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function setProgress(pct, texto, ok, erro) {
  document.getElementById('progressSection').classList.add('show');
  const bar = document.getElementById('progressBar');
  bar.style.width = pct + '%';
  bar.className = 'progress-bar' + (ok ? ' complete' : erro ? ' erro' : '');
  document.getElementById('progressLabel').textContent = texto;
}

function handleEvent(e) {
  const data = JSON.parse(e.data);
  if (data.type === 'log') {
    appendLog(data.msg, data.tipo);
  } else if (data.type === 'progresso') {
    setProgress(data.pct, data.texto, data.ok, data.erro);
    if (data.pct >= 100) {
      const btn = document.getElementById('btnInstalar');
      btn.disabled = false;
      btn.textContent = data.ok ? '✔  Instalado' : data.erro ? 'Falhou — Tentar de novo' : 'Salvar e Instalar';
      btn.classList.toggle('success', !!data.ok);
      btn.classList.toggle('erro', !!data.erro);
      if (_eventSource) { _eventSource.close(); _eventSource = null; }
      if (data.ok) setTimeout(carregarStatus, 1000);
    }
  }
}

// ── Instalar ──────────────────────────────────

async function instalar() {
  const abaBuscar = document.getElementById('tabBuscar').classList.contains('active');
  const urlApi = document.getElementById('cfgUrlApi').value.trim();
  const token = document.getElementById('cfgToken').value.trim();
  const intervalo = document.getElementById('cfgIntervalo').value;

  if (!urlApi || !token) {
    appendLog('Preencha a URL da API e o token do agente (em "Servidor").', 'err');
    return;
  }

  const btn = document.getElementById('btnInstalar');
  btn.disabled = true;
  btn.classList.remove('success', 'erro');
  document.getElementById('logBox').innerHTML = '';
  document.getElementById('logBox').classList.add('show');

  let patrimonio = null;

  if (abaBuscar) {
    if (!_maquinaSelecionada) {
      appendLog('Selecione uma máquina na lista, ou troque para "Cadastrar nova".', 'err');
      btn.disabled = false;
      return;
    }
    patrimonio = _maquinaSelecionada.patrimonio_interno;
    appendLog(`Usando máquina já cadastrada: PAT ${patrimonio}`, 'info');
  } else {
    const nome = document.getElementById('cadNome').value.trim();
    if (!nome) {
      appendLog('Nome da máquina é obrigatório.', 'err');
      btn.disabled = false;
      return;
    }
    btn.textContent = 'Cadastrando…';
    appendLog('Cadastrando nova máquina…', 'info');
    try {
      const r = await fetch('/api/cadastrar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url_api: urlApi,
          token_agente: token,
          nome_maquina: nome,
          responsavel: document.getElementById('cadResponsavel').value.trim() || null,
          locadora: document.getElementById('cadLocadora').value.trim() || null,
          categoria: document.getElementById('cadCategoria').value,
          setor: document.getElementById('cadSetor').value.trim() || null,
          numero_serie: document.getElementById('cadSerie').value.trim() || null,
        }),
      });
      const d = await r.json();
      if (!r.ok) {
        appendLog(`Erro ao cadastrar: ${d.detail || 'falha desconhecida'}`, 'err');
        btn.disabled = false;
        btn.textContent = 'Salvar e Instalar';
        return;
      }
      patrimonio = d.patrimonio;
      appendLog(`Máquina cadastrada — patrimônio ${patrimonio}`, 'ok');
    } catch (e) {
      appendLog('Não foi possível contactar o servidor BlueControl.', 'err');
      btn.disabled = false;
      btn.textContent = 'Salvar e Instalar';
      return;
    }
  }

  // Conecta o SSE antes de disparar a instalação, pra não perder os primeiros eventos
  if (_eventSource) _eventSource.close();
  _eventSource = new EventSource('/api/stream');
  _eventSource.onmessage = handleEvent;
  _eventSource.onerror = () => appendLog('Conexão com o configurador local perdida.', 'warn');

  btn.textContent = 'Instalando…';
  fetch('/api/instalar', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      patrimonio,
      url_api: urlApi,
      token_agente: token,
      intervalo_segundos: intervalo,
    }),
  }).catch(() => appendLog('Falha ao contactar o configurador local.', 'err'));
}

// ── Init ──────────────────────────────────────
carregarStatus();
