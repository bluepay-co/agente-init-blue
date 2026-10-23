# Instalador macOS (.app) — como compilar

O agente e o instalador de macOS já existem em `agents/macos/`. Falta só gerar
o **`.app` distribuível** (equivalente ao `ConfiguradorAgente.exe` do Windows).

## Por que não sai daqui (Linux)
Binário do macOS é Mach-O e o PyInstaller/py2app só o produzem **rodando no
próprio macOS**. Não há "Wine ao contrário" — então o `.app` precisa ser
compilado num Mac. O código já está pronto para isso.

## Passo a passo (num Mac)
1. Instalar as ferramentas (uma vez):
   ```bash
   xcode-select --install
   python3 -m pip install --upgrade pip
   python3 -m pip install pyinstaller pywebview pyobjc psutil requests
   ```
2. Copiar a pasta `agents/macos/` para o Mac e rodar:
   ```bash
   chmod +x compilar_mac.sh
   ./compilar_mac.sh
   ```
3. Saída: `dist/BlueControl_Agente_Mac.app` — **com o agente de métricas
   embutido** (o técnico não precisa de mais nenhum arquivo junto).

## Ao entregar nas máquinas
- O app **não é assinado/notarizado**, então o Gatekeeper bloqueia no primeiro
  uso. O técnico abre com **botão direito → Abrir**, ou remove a quarentena:
  ```bash
  xattr -dr com.apple.quarantine "/caminho/BlueControl_Agente_Mac.app"
  ```
- A instalação pede a **senha de administrador do macOS** (diálogo do sistema).
- O **patrimônio é digitado à mão** na tela do instalador (decisão atual — sem
  busca/cadastro automático como no Windows).

## O que o instalador faz na máquina
Grava `config.json`, instala o agente de métricas como `LaunchDaemon`, o agente
de VPN (detecção por MAC do gateway) + watchdog, habilita SSH e cria a VPN
L2TP/IPSec. Mesmo comportamento dos scripts `instalar_bluecontrol_mac.*`.

## Segurança (dívida conhecida)
`AGENT_SECRET` e `VPN_PSK` estão fixos em `instalar_bluecontrol_mac.py`. Já está
na lista de dívida de segurança; ao rotacionar as credenciais, trocar aqui também.
