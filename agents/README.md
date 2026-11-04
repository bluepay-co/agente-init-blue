# Agentes BlueControl

Agentes instalados nas máquinas dos clientes. Conversam com o backend do
BlueControl IT (`http://192.168.10.11:8550`, projeto separado — não é o
`src/agente.py` deste repositório) usando um token de agente no header
`X-Agent-Token`.

| Pasta | Conteúdo |
|---|---|
| `windows/` | Agente remoto e agente de métricas |
| `windows/agente_leve/` | Agente enxuto, só métricas essenciais |
| `windows/configurador/` | **Substituído** pela aba Monitoramento do agente |
| `macos/` | Equivalentes para macOS, com LaunchDaemons em `macos/launchd/` |

## Quem instala os agentes

A **aba Monitoramento** do `AgenteBlue.exe` (ver `src/monitoramento.py`). Ela
cadastra a máquina, grava `C:\BlueControl\config.json`, registra o serviço de
métricas e a tarefa do acesso remoto, e habilita o WinRM.

Os `.exe` compilados em `windows/dist/` são o que ela instala — o
`build/compilar.ps1` da raiz os copia para uma pasta `agents\` ao lado do
`AgenteBlue.exe`.

O `windows/configurador/` fazia esse papel como programa separado. Está
mantido por enquanto como plano B, até a aba ser validada em máquina real; o
código novo é o `src/monitoramento.py`, e é lá que as correções entram.

## Segredos

Nenhum segredo fica no código. Não há valor padrão: sem o segredo o agente
para em vez de falar com a API usando uma chave que está publicada no Git.

| Variável | Para quê |
|---|---|
| `BLUECONTROL_AGENT_TOKEN` | Token do agente, enviado em `X-Agent-Token` |
| `BLUECONTROL_ADMIN_PASS` | Senha do administrador local usada no bloqueio/desbloqueio remoto |
| `BLUECONTROL_SENHA_TEMP` | Senha que os usuários recebem ao serem desbloqueados |

Em tempo de execução, cada agente lê do `config.json` gravado pelo instalador:
`token_agente` (ou `agent_secret`), `senha_admin_local` e `senha_temporaria`.
Faltando qualquer um, o agente **para com erro** em vez de usar um valor padrão.

### Bloqueio e desbloqueio remoto

Os scripts `windows/scripts/bloqueio_remoto.ps1` e `desbloqueio_remoto.ps1` são
disparados pelo painel, não por este repositório. Ambos aceitam a credencial por
parâmetro e caem na variável de ambiente quando ela não vem:

```powershell
.\bloqueio_remoto.ps1    -IpLocal 192.168.10.42 -SenhaAdmin '...'
.\desbloqueio_remoto.ps1 -IpLocal 192.168.10.42 -SenhaAdmin '...' -SenhaTemporaria '...'
```

Sem a credencial eles saem com código **3** (`ERRO_SEM_CREDENCIAL`); o
desbloqueio sem senha temporária sai com **4** (`ERRO_SEM_SENHA_TEMPORARIA`).

A senha que **tranca** o usuário para fora não vem de lugar nenhum: é sorteada a
cada execução, porque ninguém precisa conhecê-la. Antes era fixa no código — e
uma senha de bloqueio publicada no Git destrava qualquer máquina da empresa.

### Instalando

```bash
# macOS
export BLUECONTROL_AGENT_TOKEN='...'
bash macos/instalar_bluecontrol_mac.sh
```

```bat
REM Windows — agente leve
set BLUECONTROL_AGENT_TOKEN=...
agente_leve\instalar_agente_lite.bat
```

```powershell
# Windows — agente leve, via PowerShell
.\agente_leve\instalar_agente_lite.ps1 -TokenAgente '...'
```

## Builds

`build/` e `dist/` são artefatos do PyInstaller e não são versionados —
versionamos só os `.spec`. Para recompilar, use os `compilar_*.sh` de cada
pasta.
