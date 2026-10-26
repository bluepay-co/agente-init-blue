# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Este projeto segue [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [4.0.0] — 2026-10-26

### Adicionado
- Aba **VPN**: instala o OpenVPN a partir do export do pfSense, grava a
  credencial do colaborador (cada um tem a sua), aponta o perfil para ela,
  **confirma conectando de verdade** e deixa a VPN subindo sozinha no boot.
  Botão de remoção que apaga credencial, perfis e o próprio OpenVPN.
- Conexão automática: o perfil vai para `config-auto` (com o CA e a chave
  `tls-auth`) e o `OpenVPNService` passa a iniciar com o Windows — antes a VPN
  só conectava quando alguém logava, abria a bandeja e clicava.
- Perfil renomeado para um nome legível no OpenVPN GUI, em vez do
  `saturno-UDP4-1194-config` gerado pelo pfSense.
- Diagnóstico de falha de conexão: o destino do perfil, a causa traduzida
  (DNS, porta recusada, sem rota, handshake vazio, arquivo que não abre) e o
  log completo do teste em `C:\ProgramData\BlueControl\`.
- Aba **Monitoramento**: AnyDesk ID detectado da própria máquina, patrimônio
  interno informado, status de estoque e senha de acesso não supervisionado.
- Botão de **excluir a máquina e limpar o agente**: apaga o cadastro no
  BlueControl via API (só a máquina cuja série bate com a lida da BIOS),
  remove serviços, tarefas e pastas.
- Número de série lido automaticamente da BIOS no cadastro.

### Corrigido
- A credencial da VPN era gravada mas o perfil não apontava para ela. Três
  bugs na mesma linha, em sequência: `re.sub` interpretando `\P` do caminho
  como escape, a escolha de um perfil órfão sem o CA ao lado, e a barra
  invertida crua — que o próprio openvpn trata como escape de shell e que
  fazia ele recusar o perfil antes de abrir socket.
- Janela invisível do instalador do OpenVPN travava a aba indefinidamente.
- `icacls` com nomes localizados (`SISTEMA`/`Administradores`) falhava no
  Windows em português e deixava o arquivo de credencial sem permissão alguma.
- O log da VPN escrevia no mesmo elemento da barra de progresso, destruindo a
  mensagem com a causa do erro.
- `#viewVpn` fora da regra flex do CSS, e `shutil` usado sem import.
- O aviso de NAT reflection aparecia em qualquer falha de rede, inclusive num
  teste via 4G, mandando investigar o firewall sem motivo.
- Um `/` solto depois de um comentário HTML aparecia como texto na aba Setup.

## [3.1.0] — 2026-10-23

### Adicionado
- Aba **Monitoramento**: cadastra a máquina no BlueControl IT (busca uma já
  cadastrada ou cadastra uma nova) e instala o agente de métricas como serviço,
  o acesso remoto como tarefa no logon, e habilita o WinRM e o ping no
  firewall. Era o `ConfiguradorAgente.exe`, um programa separado.
- `src/monitoramento.py` e rotas `GET /api/monitoramento/status`,
  `POST /api/monitoramento/{buscar,cadastrar,instalar}`.
- `AGENTEBLUE_EMBED_AGENTS=1` embute os agentes de monitoramento no executável;
  por padrão eles vão soltos numa pasta `agents\` ao lado dele.
- Cobertura no `smoke.py` para as rotas novas, o formato do `/status` e o canal
  de eventos `mon_*`.

### Modificado
- **Um executável só.** O `ConfiguradorAgente.exe` repetia a infraestrutura
  inteira do agente — servidor HTTP, fila SSE, elevação a Administrador, busca
  de porta livre e abertura da janela. Ao virar aba, sobrou apenas a lógica de
  domínio. No CSS, 14 dos 33 seletores dele foram descartados por serem cópias
  dos que já existiam (`.card` era igual byte a byte).
- Eventos SSE da aba Monitoramento saem em `mon_log`/`mon_progresso`, canal
  próprio, para não se misturarem com o log da aba Setup.

### Segurança
- As rotas de monitoramento passaram a exigir o token de sessão e as
  verificações de `Origin`/`Host`. No programa separado elas eram **abertas**:
  qualquer página no navegador da máquina podia chamar a instalação, que
  registra serviço como `LocalSystem`.

### Corrigido
- O cadastro aceita `patrimonio` e `patrimonio_interno` na resposta do
  servidor, em vez de depender de um dos dois nomes.

## [3.0.0] — 2026-10-15

### Adicionado
- Painel **Gerenciar Usuários**: listar, criar, editar, renomear (conta, pasta
  de perfil e `ProfileImagePath`) e excluir usuários locais.
- Painel **Desinstalar Programas**: desinstalação em lote com limpeza de sobras
  (pastas e chaves de registro).
- Etapa de **verificação de integridade** (`sfc /scannow`, ou `DISM
  /RestoreHealth` + SFC no modo completo).
- Remoção de bloatware e do OneDrive na etapa de otimização.
- Senha de acesso não supervisionado do AnyDesk via `config.json`.
- **Autenticação das rotas `/api/`** por token de sessão, gerado a cada
  execução e entregue à janela pela URL de abertura — sem login nem senha para
  o técnico digitar. Inclui verificação de `Origin` e `Host` (anti-CSRF e
  anti-DNS-rebinding) e `web/api.js` como camada única de acesso à API.
- `tests/smoke.py`: smoke test do servidor local (rotas, assets, token e SSE).
- Rota `GET /api/version` e `src/version.py` como fonte única da versão.
- `CHANGELOG.md` e `installers/README.md`.

### Alterado
- **Estrutura do repositório reorganizada**: código em `src/`, interface em
  `web/`, ícone e wallpaper em `assets/`, scripts de build em `build/`, e a
  antiga pasta `.exe/` renomeada para `installers/`.
- O servidor HTTP passa a servir a interface de `web/` em vez da raiz, de modo
  que `config.json` e os instaladores deixam de ser alcançáveis por HTTP.
- A versão exibida na interface passa a vir do backend, não mais fixa no HTML.

### Corrigido
- **Qualquer página aberta no navegador da máquina podia disparar as rotas do
  agente** (criar usuário administrador, desinstalar programas, rodar o setup),
  porque elas não exigiam autenticação alguma. Agora exigem o token da sessão.
- O servidor servia a raiz do projeto por HTTP, então `GET /config.json`
  entregava as senhas. A interface passou a ser servida de `web/`.
- O cabeçalho `Access-Control-Allow-Origin: *` no stream de eventos permitia que
  outra origem lesse os logs do agente; removido.
- O `config.json` **não é mais embutido no executável por padrão** — ele guarda
  senhas e o `.exe` é distribuído. Para embutir conscientemente, defina
  `AGENTEBLUE_EMBED_CONFIG=1` antes do build.
- `compilar.ps1` copiava uma pasta `apps/` que não existe mais; agora copia
  `web/`, `assets/` e `installers/`.
- A mensagem final do build dizia que o `config.json` ia embutido no onefile.
- Line endings do `compilar.ps1` normalizados (estavam como `CR CR LF`).
- `filename` do Adobe Reader no `config.example.json` agora bate com o arquivo
  real em `installers/`.

### Removido
- Artefatos de build versionados por engano (`build/AgenteBlue/`, `build_log.txt`).
- Configuração local do Claude Code (`.claude/`) do controle de versão.

## [2.0.1] — 2026-07-01

### Adicionado
- Configuração externa via `config.json`, com `config.example.json` de modelo.
- Criação/atualização de usuários locais, detectando a conta interna de
  Administrador pelo SID (`-500`) em vez do nome.

### Alterado
- Instalação de softwares passa a usar apenas instaladores locais (winget
  removido).
- Log movido para `C:\ProgramData\AgenteBlue\agente_blue.log`.

### Removido
- Teste de busca na rede e configuração de impressora por IP.

## [1.0.0] — 2026-05-15

Primeira versão: configuração de rede, SMB, instalação de softwares, papel de
parede e otimização do Windows, com interface local servida por HTTP + SSE.
