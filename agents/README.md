# Agentes BlueControl

Agentes instalados nas máquinas dos clientes. Conversam com a API do painel
(`src/agente.py`) usando um token de agente no header `X-Agent-Token`.

| Pasta | Conteúdo |
|---|---|
| `windows/` | Agente remoto, agente de métricas e o configurador |
| `windows/agente_leve/` | Agente enxuto, só métricas essenciais |
| `macos/` | Equivalentes para macOS, com LaunchDaemons em `macos/launchd/` |

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
