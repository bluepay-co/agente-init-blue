# ════════════════════════════════════════════════════════════════════════════
# BlueControl IT — Instalador do Agente Leve via PowerShell
# ════════════════════════════════════════════════════════════════════════════
# USO:
#   Executar como Administrador:
#     powershell -ExecutionPolicy Bypass -File instalar_agente_lite.ps1
#
#   Ou via GPO/PSRemoting em múltiplas máquinas:
#     Invoke-Command -ComputerName "PC-001","PC-002" -ScriptBlock { & '\\servidor\share\instalar_agente_lite.ps1' }
# ════════════════════════════════════════════════════════════════════════════

param(
    [string]$Patrimonio = "0001",
    [string]$UrlAPI = "http://192.168.10.11:8550",
    [string]$TokenAgente = $env:BLUECONTROL_AGENT_TOKEN,
    [int]$IntervalSegundos = 300
)

# ────────────────────────────────────────────────────────────────────────────
# FUNÇÕES
# ────────────────────────────────────────────────────────────────────────────

function Write-Log {
    param([string]$Mensagem, [string]$Tipo = "INFO")
    $timestamp = Get-Date -Format "HH:mm:ss"
    switch($Tipo) {
        "OK"    { Write-Host "[$timestamp] [✓] $Mensagem" -ForegroundColor Green }
        "WARN"  { Write-Host "[$timestamp] [!] $Mensagem" -ForegroundColor Yellow }
        "ERROR" { Write-Host "[$timestamp] [✗] $Mensagem" -ForegroundColor Red }
        default { Write-Host "[$timestamp] [*] $Mensagem" -ForegroundColor Cyan }
    }
}

# O token nao tem padrao no codigo. Passe -TokenAgente ou defina a variavel
# de ambiente BLUECONTROL_AGENT_TOKEN antes de rodar.
if ([string]::IsNullOrWhiteSpace($TokenAgente)) {
    Write-Log "Token do agente nao informado. Use -TokenAgente '<token>' ou defina BLUECONTROL_AGENT_TOKEN." "ERROR"
    exit 1
}

function Test-Admin {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ────────────────────────────────────────────────────────────────────────────
# VALIDAÇÕES
# ────────────────────────────────────────────────────────────────────────────

Write-Host "`n"
Write-Host "════════════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host " BlueControl IT - Agente de Monitoramento Leve" -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "`n"

if (-not (Test-Admin)) {
    Write-Log "Este script precisa ser executado como Administrador!" "ERROR"
    Write-Log "Tente: powershell -RunAs Administrator" "WARN"
    exit 1
}
Write-Log "Executando como Administrador" "OK"

# ────────────────────────────────────────────────────────────────────────────
# 1. CRIAR PASTA C:\BlueControl
# ────────────────────────────────────────────────────────────────────────────

Write-Host "`n[1/6] Criando pasta C:\BlueControl..."
$BlueControlDir = "C:\BlueControl"

if (-not (Test-Path $BlueControlDir)) {
    New-Item -ItemType Directory -Path $BlueControlDir -Force | Out-Null
    Write-Log "Pasta criada" "OK"
} else {
    Write-Log "Pasta já existe" "OK"
}

# ────────────────────────────────────────────────────────────────────────────
# 2. CRIAR config.json
# ────────────────────────────────────────────────────────────────────────────

Write-Host "`n[2/6] Configurando config.json..."
$ConfigPath = "$BlueControlDir\config.json"

if (-not (Test-Path $ConfigPath)) {
    $config = @{
        patrimonio = $Patrimonio
        url_api = $UrlAPI
        token_agente = $TokenAgente
        intervalo_segundos = $IntervalSegundos
    }
    $config | ConvertTo-Json | Out-File -FilePath $ConfigPath -Encoding UTF8
    Write-Log "config.json criado: $ConfigPath" "OK"
} else {
    Write-Log "config.json já existe (não substituindo)" "WARN"
    Write-Log "Edite manualmente: $ConfigPath" "INFO"
}

# ────────────────────────────────────────────────────────────────────────────
# 3. VERIFICAR/INSTALAR PYTHON
# ────────────────────────────────────────────────────────────────────────────

Write-Host "`n[3/6] Verificando Python..."

try {
    $PythonVersion = python --version 2>&1
    Write-Log "Python encontrado: $PythonVersion" "OK"
} catch {
    Write-Log "Python não encontrado!" "ERROR"
    Write-Log "Instale via: https://www.python.org/downloads/" "WARN"
    Write-Log "Ou: winget install Python.Python.3.11" "WARN"
    exit 1
}

# ────────────────────────────────────────────────────────────────────────────
# 4. INSTALAR DEPENDÊNCIAS
# ────────────────────────────────────────────────────────────────────────────

Write-Host "`n[4/6] Instalando dependências (psutil, requests)..."

try {
    Write-Log "Atualizando pip..." "INFO"
    python -m pip install --quiet --upgrade pip | Out-Null

    Write-Log "Instalando psutil..." "INFO"
    python -m pip install --quiet psutil | Out-Null

    Write-Log "Instalando requests..." "INFO"
    python -m pip install --quiet requests | Out-Null

    Write-Log "Dependências instaladas com sucesso" "OK"
} catch {
    Write-Log "Erro ao instalar dependências: $_" "ERROR"
    exit 1
}

# ────────────────────────────────────────────────────────────────────────────
# 5. COPIAR ARQUIVO DO AGENTE
# ────────────────────────────────────────────────────────────────────────────

Write-Host "`n[5/6] Preparando agente..."

# Procura o arquivo na pasta atual ou em localizações conhecidas
$PossibleLocations = @(
    ".\agente_monitoramento_leve.py",
    "$PSScriptRoot\agente_monitoramento_leve.py",
    ".\agente_monitoramento_leve.py"
)

$AgentSourcePath = $null
foreach ($loc in $PossibleLocations) {
    if (Test-Path $loc) {
        $AgentSourcePath = Resolve-Path $loc
        break
    }
}

if ($AgentSourcePath) {
    Copy-Item -Path $AgentSourcePath -Destination "$BlueControlDir\agente_monitoramento_leve.py" -Force
    Write-Log "Agente copiado para: $BlueControlDir" "OK"
} else {
    Write-Log "Arquivo agente_monitoramento_leve.py não encontrado!" "WARN"
    Write-Log "Se precisar compilar para EXE, execute:" "INFO"
    Write-Log "pyinstaller --onefile --noconsole --name BlueControl_Lite agente_monitoramento_leve.py" "INFO"
}

# ────────────────────────────────────────────────────────────────────────────
# 6. INSTALAR COMO SERVIÇO WINDOWS
# ────────────────────────────────────────────────────────────────────────────

Write-Host "`n[6/6] Instalando Serviço Windows..."

$ServiceName = "BlueControlLite"
$ServiceDisplayName = "BlueControl IT - Agente Leve"

# Verifica se serviço já existe
$ExistingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

if ($ExistingService) {
    Write-Log "Serviço já existe, parando..." "WARN"
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue | Out-Null
    Start-Sleep -Seconds 2
    Remove-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue | Out-Null
    Write-Log "Serviço antigo removido" "OK"
}

# Cria novo serviço
$PythonPath = (Get-Command python.exe).Source
$BinPath = "$PythonPath $BlueControlDir\agente_monitoramento_leve.py"

try {
    New-Service -Name $ServiceName `
                -DisplayName $ServiceDisplayName `
                -BinaryPathName $BinPath `
                -StartupType Automatic `
                -ErrorAction Stop | Out-Null

    Write-Log "Serviço criado: $ServiceName" "OK"

    # Inicia o serviço
    Write-Log "Iniciando serviço..." "INFO"
    Start-Service -Name $ServiceName -ErrorAction Stop
    Start-Sleep -Seconds 2

    $ServiceStatus = (Get-Service -Name $ServiceName).Status
    if ($ServiceStatus -eq "Running") {
        Write-Log "Serviço iniciado com sucesso" "OK"
    } else {
        Write-Log "Serviço criado mas ainda não está rodando (status: $ServiceStatus)" "WARN"
    }

} catch {
    Write-Log "Erro ao criar serviço: $_" "ERROR"
    Write-Log "Tente manualmente:" "INFO"
    Write-Log "sc create $ServiceName binpath= `"$BinPath`" start= auto" "INFO"
}

# ────────────────────────────────────────────────────────────────────────────
# RESUMO FINAL
# ────────────────────────────────────────────────────────────────────────────

Write-Host "`n"
Write-Host "════════════════════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host " ✓ Instalação Concluída!" -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "`n"

Write-Log "Arquivo de Configuração: $ConfigPath"
Write-Log "Log do Agente: $BlueControlDir\lite.log"

Write-Host "`nPróximos passos:" -ForegroundColor Yellow
Write-Host "1. Edite: $ConfigPath"
Write-Host "   - patrimonio: número do equipamento"
Write-Host "   - url_api: endereço da API BlueControl"
Write-Host "`n2. Reinicie o serviço:"
Write-Host "   Restart-Service BlueControlLite`n"
Write-Host "3. Verifique o log:"
Write-Host "   Get-Content $BlueControlDir\lite.log -Wait`n"

Write-Host "Comandos úteis:" -ForegroundColor Yellow
Write-Host "   # Ver status"
Write-Host "   Get-Service BlueControlLite`n"
Write-Host "   # Parar"
Write-Host "   Stop-Service BlueControlLite`n"
Write-Host "   # Iniciar"
Write-Host "   Start-Service BlueControlLite`n"
Write-Host "   # Remover"
Write-Host "   Remove-Service BlueControlLite`n"

Write-Host "════════════════════════════════════════════════════════════════════════════`n" -ForegroundColor Cyan
