// ============================================
//  Agente Blue — api.js
//  Camada única de acesso à API local.
//
//  O agente roda como Administrador e expõe rotas que criam usuários e
//  desinstalam programas. Para que só ESTA janela possa chamá-las, o backend
//  sorteia um token a cada execução e o entrega na URL de abertura. Guardamos
//  o token e o reenviamos em toda requisição.
//
//  Carregue este arquivo ANTES dos demais scripts.
// ============================================

const API_TOKEN = (() => {
  const daUrl = new URLSearchParams(window.location.search).get('t');
  if (daUrl) {
    // Sobrevive a um F5: a URL é limpa logo abaixo, mas a aba mantém o valor.
    try { sessionStorage.setItem('agenteToken', daUrl); } catch (e) { /* modo restrito */ }
    // Tira o token da barra de endereço (e do cabeçalho Referer).
    try {
      window.history.replaceState({}, '', window.location.pathname);
    } catch (e) { /* ignora */ }
    return daUrl;
  }
  try { return sessionStorage.getItem('agenteToken') || ''; } catch (e) { return ''; }
})();

// fetch com o token no cabeçalho.
function apiFetch(url, opcoes) {
  const o = Object.assign({}, opcoes);
  o.headers = Object.assign({}, o.headers, { 'X-Agente-Token': API_TOKEN });
  return fetch(url, o);
}

// POST JSON — devolve o corpo já desserializado.
async function apiPost(url, corpo) {
  const r = await apiFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(corpo || {})
  });
  return await r.json();
}

// GET JSON.
async function apiGet(url) {
  const r = await apiFetch(url);
  return await r.json();
}

// EventSource não aceita cabeçalhos customizados — aqui o token vai na query.
function apiStream(caminho) {
  return new EventSource(`${caminho}?t=${encodeURIComponent(API_TOKEN)}`);
}
