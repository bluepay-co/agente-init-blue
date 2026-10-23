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

Em tempo de execução, cada agente lê `token_agente` (ou `agent_secret`) do
`config.json` gravado pelo instalador.

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
