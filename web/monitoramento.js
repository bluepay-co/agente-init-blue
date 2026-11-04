// ============================================
//  Agente Blue — monitoramento.js
//  Aba "Monitoramento": cadastra a máquina no BlueControl IT e instala o
//  agente de métricas + acesso remoto.
//
//  Veio do ConfiguradorAgente.exe, que era um programa separado. Duas
//  adaptações ao entrar aqui:
//
//  1. Tudo com prefixo "mon". Os scripts da interface compartilham o escopo
//     global, e appendLog/setProgress/handleEvent/_eventSource já são do
//     script.js — um `let _eventSource` repetido seria SyntaxError e levaria
//     a página inteira.
//  2. Chama a API pelo api.js (apiGet/apiPost/apiStream), que reenvia o token
//     da sessão. As rotas /api/monitoramento/ rodam como Administrador e
//     exigem esse token.
// ============================================

let _monEventSource = null;
let _monSelecionada = null;   // {patrimonio_interno, modelo, ...} quando veio da busca
let _monBuscaTimer = null;
let _monResultados = [];
let _monPrefilled = false;    // URL/token já preenchidos uma vez (ver monCarregarStatus)
let _monLimpando = false;     // limpeza em curso: o progresso é dela, não da instalação
let _monPollTimer = null;     // polling de progresso (substitui SSE)
let _monLogCount = 0;         // quantas linhas de log já foram exibidas

// ── Status atual ─────────────────────────────

async function monCarregarStatus() {
  try {
    const d = await apiGet('/api/monitoramento/status');

    // Os campos avançados vêm ANTES do early return: numa máquina nova
    // (existe = false) eles são a única fonte de URL e token para buscar e
    // cadastrar. O /status devolve os padrões quando não há config.
    //
    // Só preenche na PRIMEIRA carga: showView() chama esta função a cada
    // entrada na aba, e sem o _monPrefilled uma URL que o técnico tivesse
    // ajustado voltava ao padrão só por ele visitar outra aba e retornar.
    if (!_monPrefilled) {
      if (d.url_api)      document.getElementById('monCfgUrlApi').value = d.url_api;
      if (d.token_agente) document.getElementById('monCfgToken').value  = d.token_agente;
      if (d.intervalo_segundos) {
        document.getElementById('monCfgIntervalo').value = String(d.intervalo_segundos);
      }

      // Avisa se os .exe dos agentes não vieram com este build — melhor saber
      // agora do que no meio da instalação. Uma vez, não a cada troca de aba.
      const faltando = Object.entries(d.agentes_disponiveis || {})
        .filter(([, ok]) => !ok).map(([nome]) => nome);
      if (faltando.length) {
        monLog(`Atenção: ${faltando.join(', ')} não encontrado(s). Coloque a pasta `
             + `agents\\ ao lado do AgenteBlue.exe.`, 'warn');
      }
      _monPrefilled = true;
    }

    // Nº de série lido da BIOS. Fica fora do _monPrefilled porque é o campo de
    // cadastro, não de configuração: se o técnico ainda não digitou nada ali,
    // preenche; se digitou (BIOS com lixo, série da etiqueta), não mexe.
    const serie = document.getElementById('monCadSerie');
    if (d.numero_serie && serie && !serie.value.trim()) {
      serie.value = d.numero_serie;
      serie.classList.add('auto-detectado');
      serie.title = 'Lido da BIOS desta máquina — edite se não bater com a etiqueta.';
      const nota = document.getElementById('monSerieNota');
      if (nota) {
        nota.textContent = 'detectado automaticamente';
        nota.style.display = 'inline';
      }
    }

    // AnyDesk ID, mesma regra da série. Fica fora do _monPrefilled de propósito:
    // numa máquina nova o AnyDesk só é instalado pela aba Setup, então na
    // primeira visita o ID ainda não existe e aparece quando a aba for reaberta.
    const ad = document.getElementById('monCadAnydesk');
    if (d.anydesk_id && ad && !ad.value.trim()) {
      ad.value = d.anydesk_id;
      ad.classList.add('auto-detectado');
      ad.title = 'Lido do AnyDesk instalado nesta máquina.';
      const notaAd = document.getElementById('monAnydeskNota');
      if (notaAd) {
        notaAd.textContent = 'detectado automaticamente';
        notaAd.style.display = 'inline';
      }
    }

    // Esconde de volta: depois da limpeza o /status responde existe=false, e
    // sem isto o card continuaria na tela anunciando uma máquina que acabou
    // de ser removida.
    if (!d.existe) {
      document.getElementById('monStatusCard').style.display = 'none';
      return;
    }

    document.getElementById('monStatusCard').style.display = 'block';
    document.getElementById('monStatusPatrimonio').textContent = `Patrimônio ${d.patrimonio}`;
    const detalhe = {
      rodando:       'Serviço rodando — ' + d.url_api,
      parado:        'Serviço parado — ' + d.url_api,
      nao_instalado: 'Configurado, serviço não instalado',
      desconhecido:  d.url_api,
    };
    document.getElementById('monStatusDetalhe').textContent = detalhe[d.servico] || d.url_api;
    document.getElementById('monStatusDot').className = 'status-dot ' + (
      d.servico === 'rodando' ? 'on' : d.servico === 'parado' ? 'off' : 'none');
  } catch (e) {
    // Card informativo: se o servidor local não respondeu, não vale alarme.
  }
}

// ── Sub-abas (já cadastrada / cadastrar nova) ─

function monMudarTab(tab) {
  document.getElementById('monTabBtnBuscar').classList.toggle('active', tab === 'buscar');
  document.getElementById('monTabBtnCadastrar').classList.toggle('active', tab === 'cadastrar');
  document.getElementById('monTabBuscar').classList.toggle('active', tab === 'buscar');
  document.getElementById('monTabCadastrar').classList.toggle('active', tab === 'cadastrar');
}

function monToggleAvancado() {
  const el = document.getElementById('monAvancadoFields');
  const mostrar = el.style.display === 'none';
  el.style.display = mostrar ? 'flex' : 'none';
  document.getElementById('monAvancadoToggle').textContent = mostrar ? 'ocultar' : 'mostrar';
}

// ── Busca de máquina existente ───────────────

function _monEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function monOnBuscaInput() {
  clearTimeout(_monBuscaTimer);
  const q = document.getElementById('monInpBusca').value.trim();
  document.getElementById('monSelecionadoBanner').classList.remove('show');
  _monSelecionada = null;
  if (q.length < 2) {
    document.getElementById('monResultados').innerHTML = '';
    return;
  }
  _monBuscaTimer = setTimeout(() => monBuscarMaquinas(q), 350);
}

async function monBuscarMaquinas(q) {
  const box    = document.getElementById('monResultados');
  const urlApi = document.getElementById('monCfgUrlApi').value.trim();
  const token  = document.getElementById('monCfgToken').value.trim();

  // Sem token a busca volta 401 e o motivo não aparece. Diz qual campo falta.
  if (!urlApi || !token) {
    box.innerHTML = '<div class="resultado-vazio">Preencha a URL da API e o token '
                  + 'do agente em "Servidor".</div>';
    return;
  }

  box.innerHTML = '<div class="resultado-vazio">Buscando…</div>';
  try {
    const d = await apiPost('/api/monitoramento/buscar',
                            { url_api: urlApi, token_agente: token, q });
    if (d.detail) {
      box.innerHTML = `<div class="resultado-vazio">Erro: ${_monEsc(d.detail)}</div>`;
      return;
    }
    // Sem "dados" e sem "detail" a resposta não é a que esperamos — dizer
    // "nenhuma máquina encontrada" aqui mandaria o técnico procurar o problema
    // no termo de busca, quando ele está no servidor.
    if (!Array.isArray(d.dados)) {
      box.innerHTML = '<div class="resultado-vazio">Resposta inesperada do '
                    + 'BlueControl — confira a URL da API em "Servidor".</div>';
      return;
    }
    if (!d.dados.length) {
      box.innerHTML = '<div class="resultado-vazio">Nenhuma máquina encontrada '
                    + 'com esse termo.</div>';
      return;
    }
    _monResultados = d.dados;
    box.innerHTML = d.dados.map((m, i) => `
      <div class="resultado-item" data-idx="${i}" onclick="monSelecionarMaquina(${i})">
        <span class="resultado-nome">${_monEsc(m.modelo || m.patrimonio_interno)}</span>
        <span class="resultado-meta">PAT ${_monEsc(m.patrimonio_interno)} · ${_monEsc(m.responsavel || 'sem responsável')}</span>
      </div>
    `).join('');
  } catch (e) {
    box.innerHTML = '<div class="resultado-vazio">Não foi possível contactar o '
                  + 'servidor BlueControl.</div>';
  }
}

function monSelecionarMaquina(idx) {
  const m = _monResultados[idx];
  if (!m) return;
  _monSelecionada = m;
  document.querySelectorAll('#monResultados .resultado-item')
          .forEach(el => el.classList.remove('selected'));
  const alvo = document.querySelector(`#monResultados .resultado-item[data-idx="${idx}"]`);
  if (alvo) alvo.classList.add('selected');
  document.getElementById('monSelecionadoBanner').classList.add('show');
  document.getElementById('monSelecionadoNome').textContent =
    `${m.modelo || m.patrimonio_interno} (PAT ${m.patrimonio_interno})`;
}

// ── Log / progresso (canal mon_*, só desta aba) ─

function monLog(msg, tipo) {
  const box = document.getElementById('monLogBox');
  if (!box) return;
  box.classList.add('show');
  const linha = document.createElement('span');
  linha.className = 'log-line ' + (tipo || 'muted');
  linha.textContent = msg;
  box.appendChild(linha);
  box.scrollTop = box.scrollHeight;
}

function monSetProgress(pct, texto, ok, erro) {
  document.getElementById('monProgressSection').classList.add('show');
  const bar = document.getElementById('monProgressBar');
  bar.style.width = pct + '%';
  bar.className = 'progress-bar' + (ok ? ' complete' : erro ? ' erro' : '');
  document.getElementById('monProgressLabel').textContent = texto;
}

// ── Polling de progresso (substitui SSE para esta aba) ───────────────────────

function _monIniciarPolling() {
  _monPararPolling();
  _monLogCount = 0;
  _monPollTimer = setInterval(_monPollProgresso, 1200);
}

function _monPararPolling() {
  if (_monPollTimer) { clearInterval(_monPollTimer); _monPollTimer = null; }
}

async function _monPollProgresso() {
  try {
    const d = await apiGet('/api/monitoramento/progresso');

    // Exibe as linhas de log novas (que ainda não foram mostradas)
    const logs = d.logs || [];
    for (let i = _monLogCount; i < logs.length; i++) {
      monLog(logs[i].msg, logs[i].tipo);
    }
    _monLogCount = logs.length;

    // Atualiza a barra de progresso
    monSetProgress(d.pct, d.texto, d.ok, d.erro);

    if (d.pct >= 100) {
      _monPararPolling();
      if (_monLimpando) {
        const btnL = document.getElementById('monBtnLimpar');
        btnL.disabled = false;
        btnL.textContent = d.erro ? 'FALHOU — TENTAR DE NOVO'
                                  : 'Excluir máquina e limpar o agente';
        _monLimpando = false;
      } else {
        const btn = document.getElementById('monBtnInstalar');
        btn.disabled = false;
        btn.textContent = d.ok ? '✔  INSTALADO'
                        : d.erro ? 'FALHOU — TENTAR DE NOVO'
                        : 'SALVAR E INSTALAR';
        btn.classList.toggle('success', !!d.ok);
        btn.classList.toggle('erro', !!d.erro);
      }
      if (d.ok) setTimeout(monCarregarStatus, 1000);
    }
  } catch (_) {
    // Falha de rede: o próximo tick tenta de novo
  }
}

// ── Instalar ──────────────────────────────────

async function monInstalar() {
  const abaBuscar = document.getElementById('monTabBuscar').classList.contains('active');
  const urlApi    = document.getElementById('monCfgUrlApi').value.trim();
  const token     = document.getElementById('monCfgToken').value.trim();
  const intervalo = document.getElementById('monCfgIntervalo').value;
  const btn       = document.getElementById('monBtnInstalar');

  if (!urlApi || !token) {
    monToggleAvancadoSeOculto();
    monLog('Preencha a URL da API e o token do agente (em "Servidor").', 'err');
    return;
  }

  btn.disabled = true;
  btn.classList.remove('success', 'erro');
  const box = document.getElementById('monLogBox');
  box.innerHTML = '';
  box.classList.add('show');

  const payload = {
    url_api: urlApi,
    token_agente: token,
    intervalo_segundos: intervalo,
  };

  if (abaBuscar) {
    if (!_monSelecionada) {
      monLog('Selecione uma máquina na lista, ou troque para "Cadastrar nova".', 'err');
      btn.disabled = false;
      return;
    }
    payload.patrimonio = _monSelecionada.patrimonio_interno;
    monLog(`Usando máquina já cadastrada: PAT ${payload.patrimonio}`, 'info');
  } else {
    const nome = document.getElementById('monCadNome').value.trim();
    if (!nome) {
      monLog('Nome da máquina é obrigatório.', 'err');
      btn.disabled = false;
      return;
    }
    payload.nome_maquina = nome;
    payload.responsavel  = document.getElementById('monCadResponsavel').value.trim() || null;
    payload.locadora     = document.getElementById('monCadLocadora').value.trim() || null;
    payload.categoria    = document.getElementById('monCadCategoria').value;
    payload.setor        = document.getElementById('monCadSetor').value.trim() || null;
    payload.numero_serie = document.getElementById('monCadSerie').value.trim() || null;
    payload.patrimonio_interno = document.getElementById('monCadPatrimonio').value.trim() || null;
    payload.status_estoque     = document.getElementById('monCadStatus').value;
    payload.anydesk_id         = document.getElementById('monCadAnydesk').value.trim() || null;
  }

  btn.textContent = 'CADASTRANDO E INSTALANDO…';
  monLog('Enviando dados ao agente…', 'info');
  monSetProgress(5, 'Cadastrando e instalando…');

  try {
    const r = await apiPost('/api/monitoramento/cadastrar_e_instalar', payload);
    if (r.patrimonio) {
      monLog(`Patrimônio: ${r.patrimonio} — instalação em andamento…`, 'ok');
      btn.textContent = 'INSTALANDO…';
      _monIniciarPolling();
    } else if (r.detail) {
      monLog(`Erro: ${r.detail}`, 'err');
      monSetProgress(100, 'Falha no cadastro', false, true);
      btn.disabled = false;
      btn.textContent = 'SALVAR E INSTALAR';
    }
  } catch (e) {
    monLog(`Falha ao contactar o agente local: ${e.message || e}`, 'err');
    monSetProgress(100, 'Falha ao contactar o agente', false, true);
    btn.disabled = false;
    btn.textContent = 'SALVAR E INSTALAR';
  }
}

// Abre o bloco "Servidor" quando o erro está lá dentro — senão o técnico lê
// "preencha a URL" sem ver campo nenhum.
function monToggleAvancadoSeOculto() {
  const el = document.getElementById('monAvancadoFields');
  if (el && el.style.display === 'none') monToggleAvancado();
}


// ── Excluir máquina + limpar o agente ──────────────────────────────────────
// Confirmação por digitação, não um "tem certeza?": a ação apaga métricas e
// histórico no servidor e não tem desfazer. Digitar o patrimônio obriga a
// olhar qual máquina está na frente, que é o erro que se quer evitar.
async function monPedirLimpeza() {
  const patrimonio = (document.getElementById('monStatusPatrimonio').textContent || '')
    .replace('Patrimônio', '').trim();
  if (!patrimonio || patrimonio === '—') {
    monLog('Não há máquina instalada nesta estação para remover.', 'warn');
    return;
  }

  const digitado = prompt(
    `Isto apaga DEFINITIVAMENTE o patrimônio ${patrimonio} do BlueControl ` +
    `(cadastro, métricas, eventos e inventário) e remove o agente desta máquina.\n\n` +
    `Não há como desfazer.\n\nDigite ${patrimonio} para confirmar:`);
  if (digitado === null) return;
  if (digitado.trim().toUpperCase() !== patrimonio.toUpperCase()) {
    monLog('Confirmação não bateu com o patrimônio — nada foi feito.', 'warn');
    return;
  }

  const btn = document.getElementById('monBtnLimpar');
  btn.disabled = true;
  btn.textContent = 'REMOVENDO…';
  _monLimpando = true;

  // Servidor primeiro: tenta descadastrar do BlueControl IT. Se falhar (IP
  // errado, rede fora, token inválido), pergunta se limpa só localmente.
  let excluidoDoServidor = false;
  try {
    const d = await apiPost('/api/monitoramento/excluir', {
      url_api: document.getElementById('monCfgUrlApi').value.trim(),
      token_agente: document.getElementById('monCfgToken').value.trim(),
      patrimonio,
    });
    if (d.patrimonio || d.patrimonio_interno) {
      monLog(`Cadastro ${d.patrimonio || d.patrimonio_interno} removido do BlueControl`, 'ok');
      excluidoDoServidor = true;
    } else {
      monLog(`Não removi do BlueControl: ${d.detail || 'erro desconhecido'}`, 'err');
    }
  } catch (e) {
    monLog('Não consegui contactar o servidor do BlueControl.', 'err');
  }

  if (!excluidoDoServidor) {
    if (!confirm('Não consegui remover o cadastro do BlueControl IT.\n\n'
               + 'Quer limpar o agente local mesmo assim?\n'
               + '(O cadastro continua no painel — remova por lá depois.)')) {
      btn.disabled = false;
      btn.textContent = 'Excluir máquina e limpar o agente';
      _monLimpando = false;
      return;
    }
    monLog('Prosseguindo com a limpeza local — remova o cadastro pelo painel.', 'warn');
  }

  try {
    await apiPost('/api/monitoramento/limpar', {});
    _monIniciarPolling();
  } catch (e) {
    monLog('Falha ao iniciar a limpeza local.', 'err');
    btn.disabled = false;
    btn.textContent = 'Excluir máquina e limpar o agente';
    _monLimpando = false;
  }
}
