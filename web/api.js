// ============================================
//  Agente Blue — api.js
//  Acesso à API local. Carregue antes dos demais scripts.
//
//  O backend sorteia um token por execução e o entrega na URL de abertura,
//  para que só esta janela possa chamar as rotas /api/ (que rodam como
//  Administrador). Guardamos o token e o reenviamos em toda requisição.
// ============================================

const API_TOKEN = (() => {
  const daUrl = new URLSearchParams(window.location.search).get('t');
  if (daUrl) {
    // sessionStorage: sobrevive ao F5, já que a URL é limpa em seguida.
    try { sessionStorage.setItem('agenteToken', daUrl); } catch (e) { /* modo restrito */ }
    try { window.history.replaceState({}, '', window.location.pathname); } catch (e) { /* ignora */ }
    return daUrl;
  }
  try { return sessionStorage.getItem('agenteToken') || ''; } catch (e) { return ''; }
})();

function apiFetch(url, opcoes) {
  const o = Object.assign({}, opcoes);
  o.headers = Object.assign({}, o.headers, { 'X-Agente-Token': API_TOKEN });
  return fetch(url, o);
}

async function apiPost(url, corpo) {
  const r = await apiFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(corpo || {})
  });
  return await r.json();
}

async function apiGet(url) {
  const r = await apiFetch(url);
  return await r.json();
}

// EventSource não aceita cabeçalhos: aqui o token vai na query.
function apiStream(caminho) {
  return new EventSource(`${caminho}?t=${encodeURIComponent(API_TOKEN)}`);
}
