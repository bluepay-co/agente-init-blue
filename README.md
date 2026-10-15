# Agente Blue — Automação Windows

O **Agente Blue** é uma ferramenta standalone que automatiza a configuração
técnica de estações de trabalho Windows: instala os softwares padrão, ajusta
rede e SMB, cria os usuários locais, otimiza o sistema e verifica a integridade
da instalação — tudo por uma interface local, com progresso e log em tempo real.

Python puro (apenas biblioteca padrão), empacotado com PyInstaller.

## 🚀 O que ele faz

Ao clicar em **Executar**, na ordem:

1. **Softwares** — instala silenciosamente os instaladores de `installers/`,
   pulando o que já está presente na máquina.
2. **Papel de parede** — aplica `assets/Fundo de Tela.*` no usuário logado.
3. **Rede** — ativa Descoberta de Rede e Compartilhamento de Arquivos em redes
   Privadas e desativa em Públicas.
4. **SMB** — configura o cliente para dispensar a assinatura de segurança.
5. **Usuários** — cria/atualiza os usuários locais definidos no `config.json`.
   A conta interna de Administrador é detectada pelo SID, não pelo nome, então
   funciona em Windows de qualquer idioma.
6. **Otimização** — plano de energia, modo escuro, barra de tarefas, remoção de
   bloatware e do OneDrive, limpeza de temporários e do cache do Windows Update.
7. **Integridade** — `sfc /scannow` (rápido) ou `DISM /RestoreHealth` + SFC
   (completo).

Além do setup, a interface tem dois painéis:

- **Usuários** — criar, editar, renomear e excluir contas locais.
- **Programas** — desinstalar programas em lote, com limpeza de sobras.

## 🔒 Segurança da interface

O agente roda como Administrador e expõe rotas que criam usuários e desinstalam
programas. Para que só a janela do agente possa usá-las:

- a cada execução o backend sorteia um **token** em memória e o entrega à janela
  pela própria URL de abertura;
- a interface guarda esse token e o reenvia em toda chamada (cabeçalho
  `X-Agente-Token`; no stream de eventos, na query `?t=`);
- requisições sem o token, ou vindas de outra origem/host, recebem **403**.

Nada disso aparece para quem usa: **não há login nem senha para digitar** — o
técnico abre o `.exe` e trabalha normalmente. O token morre junto com o processo.

## 📁 Estrutura do projeto

```
agente-blue/
├── src/                  código Python
│   ├── agente.py         entrypoint: servidor HTTP, SSE e etapas do setup
│   ├── usuarios.py       painel Gerenciar Usuários
│   ├── programas.py      painel Desinstalar Programas
│   └── version.py        fonte única da versão
├── web/                  interface (HTML, CSS, JS; api.js fala com o backend)
├── assets/               ícone e papel de parede
├── installers/           instaladores usados na etapa de softwares
├── build/                AgenteBlue.spec + compilar.ps1
├── tests/                smoke test do servidor local
├── config.example.json   modelo de configuração
└── CHANGELOG.md
```

A mesma estrutura se repete ao lado do `AgenteBlue.exe` compilado, então editar
`web/` ou trocar um instalador não exige recompilar (no modo onedir).

## ⚙️ Configuração (`config.json`)

Credenciais, usuários e a lista de softwares ficam num **`config.json`** fora do
código-fonte e fora do Git.

1. Copie **`config.example.json`** para **`config.json`** na raiz do projeto
   (ou ao lado do `AgenteBlue.exe`).
2. Preencha as senhas do administrador, dos usuários e do AnyDesk.
3. Confira os `filename` dos softwares contra os arquivos em `installers/`.

> O `config.json` **não é versionado** e, por padrão, **não é embutido no
> executável** — ele contém senhas. Para embutir mesmo assim, defina
> `AGENTEBLUE_EMBED_CONFIG=1` antes de compilar.

## 📦 Instaladores

A pasta `installers/` precisa conter os executáveis referenciados no
`config.json`. Dois deles passam de 100 MB e ficam fora do Git — veja
[`installers/README.md`](installers/README.md) para a lista e onde baixar.

## 📥 Como usar

1. Copie `AgenteBlue.exe` e o `config.json` preenchido para a máquina.
2. Clique com o botão direito no `.exe` e escolha **"Executar como
   Administrador"**.
   - *Se o Windows exibir "Fornecedor Desconhecido", clique em "Mais
     Informações" → "Executar assim mesmo".*

Log completo em `C:\ProgramData\AgenteBlue\agente_blue.log`.

## 🛠️ Para desenvolvedores

Rodar direto do código-fonte (precisa ser Windows, como Administrador):

```powershell
python src\agente.py
```

Compilar (a partir da raiz do projeto):

```powershell
.\build\compilar.ps1 -Onefile      # entrega: dist\AgenteBlue.exe (arquivo único)
.\build\compilar.ps1               # dev:     dist\AgenteBlue\AgenteBlue.exe + _internal\
.\build\compilar.ps1 -AssetsOnly   # só atualiza web/, assets/ e installers/ (não recompila)
```

Com GNU Make disponível: `make`, `make onefile`, `make assets`, `make dev`,
`make clean`.

Teste rápido do servidor local (não executa nenhuma etapa do setup, roda em
qualquer sistema):

```
python tests/smoke.py
```

Ao lançar uma versão, atualize `src/version.py` e registre em `CHANGELOG.md`.

---
*Desenvolvido por BlueControl IT*
