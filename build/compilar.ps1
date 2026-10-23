# compilar.ps1 — Agente Blue
#
# Uso (rode a partir da raiz do projeto):
#   .\build\compilar.ps1               # ONEDIR (dev/testes) -> dist\AgenteBlue\AgenteBlue.exe + _internal\
#   .\build\compilar.ps1 -AssetsOnly   # so atualiza web/assets/installers no onedir (NAO recompila)
#   .\build\compilar.ps1 -Onefile      # ONEFILE (entrega)   -> dist\AgenteBlue.exe (arquivo unico)
#
# Sempre sobrescreve o mesmo destino. Nunca gera _old_, dist_new nem copias com timestamp.

param(
    [switch]$AssetsOnly,
    [switch]$Onefile
)

$ErrorActionPreference = 'Stop'

# Este script vive em build\; todos os caminhos sao relativos a RAIZ do projeto.
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$AppName    = 'AgenteBlue'
$SpecFile   = Join-Path $PSScriptRoot 'AgenteBlue.spec'
$DistDir    = Join-Path $Root 'dist'
$OutDir     = Join-Path $DistDir $AppName            # saida onedir (pasta)
$ExePath    = Join-Path $OutDir "$AppName.exe"
$OnefileExe = Join-Path $DistDir "$AppName.exe"      # saida onefile (arquivo unico)
# Pastas copiadas soltas ao lado do exe no modo ONEDIR (editaveis sem recompilar).
$AssetDirs  = @('web', 'assets', 'installers')
# Arquivos soltos. config.json fica de fora de proposito: guarda senhas e e
# criado/preenchido pelo tecnico direto na pasta de destino.
$AssetFiles = @('config.example.json')

if ($AssetsOnly -and $Onefile) {
    Write-Host " [ERRO] -AssetsOnly nao se aplica ao onefile (assets ficam embutidos no exe)." -ForegroundColor Red
    exit 1
}

$modo = if ($Onefile) { 'ONEFILE (entrega)' } else { 'ONEDIR (dev)' }

function Write-Step($n, $msg) { Write-Host " [$n] $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host " ===================================================" -ForegroundColor Cyan
Write-Host "   AGENTE BLUE  |  COMPILADOR  -  $modo" -ForegroundColor Cyan
Write-Host " ===================================================" -ForegroundColor Cyan
Write-Host ""

# ── Encerrar instâncias em execução (libera arquivos travados) ──────────
Write-Step '1/4' 'Encerrando instancias do Agente...'
$names = @('AgenteBlue', 'AgenteBlue_PRO', 'AgenteBlue2', 'AgenteBlue_old')
foreach ($n in $names) {
    Get-Process -Name $n -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 800

function Copy-Assets {
    if (-not (Test-Path $OutDir)) {
        throw "Pasta de saida nao existe ($OutDir). Rode o build completo primeiro (sem -AssetsOnly)."
    }
    foreach ($d in $AssetDirs) {
        $src = Join-Path $Root $d
        if (Test-Path $src) {
            $dst = Join-Path $OutDir $d
            if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
            Copy-Item $src -Destination $dst -Recurse -Force
        }
    }
    foreach ($f in $AssetFiles) {
        $src = Join-Path $Root $f
        if (Test-Path $src) { Copy-Item $src -Destination $OutDir -Force }
    }
    Write-Host "  [OK] Assets atualizados em $OutDir" -ForegroundColor Green
}

# Agentes de monitoramento (aba Monitoramento). Vao SOLTOS numa pasta agents\
# ao lado do exe, que e onde o _resolver() procura primeiro -- assim trocar o
# agente nao exige recompilar o inicializador. Com AGENTEBLUE_EMBED_AGENTS=1
# eles entram dentro do exe e esta copia e dispensavel.
function Copy-Agents([string]$Destino) {
    $src = Join-Path $Root 'agents\windows\dist'
    $exes = @(if (Test-Path $src) { Get-ChildItem -Path $src -Filter '*.exe' -File })
    if (-not $exes) {
        Write-Host "  [AVISO] Nenhum agente em agents\windows\dist\ -- a aba" -ForegroundColor Yellow
        Write-Host "          Monitoramento vai avisar que faltam os .exe." -ForegroundColor Yellow
        return
    }
    if ($env:AGENTEBLUE_EMBED_AGENTS -eq '1') {
        Write-Host "  [OK] $($exes.Count) agente(s) embutido(s) no exe." -ForegroundColor Green
        return
    }
    $dst = Join-Path $Destino 'agents'
    if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Path $dst | Out-Null }
    foreach ($e in $exes) { Copy-Item $e.FullName -Destination $dst -Force }
    $mb = [math]::Round((($exes | Measure-Object Length -Sum).Sum) / 1MB, 1)
    Write-Host "  [OK] $($exes.Count) agente(s) em $dst ($mb MB)" -ForegroundColor Green
}

# ── Modo rápido: só atualiza os arquivos, sem recompilar ────────────────
if ($AssetsOnly) {
    Write-Host " Modo -AssetsOnly: atualizando arquivos sem recompilar..." -ForegroundColor Cyan
    Copy-Assets
    Copy-Agents $OutDir
    Write-Host ""
    Write-Host " Pronto. Execute: $ExePath" -ForegroundColor Green
    exit 0
}

# ── Build PyInstaller em pasta temporária ───────────────────────────────
Write-Step '2/4' "Compilando ($modo)..."
$work    = Join-Path $env:TEMP 'agenteblue_build'
$tmpDist = Join-Path $env:TEMP 'agenteblue_dist'
Remove-Item -Recurse -Force $work, $tmpDist -ErrorAction SilentlyContinue

if ($Onefile) { $env:AGENTEBLUE_ONEFILE = '1' } else { Remove-Item Env:\AGENTEBLUE_ONEFILE -ErrorAction SilentlyContinue }
& python -m PyInstaller --noconfirm --clean --distpath $tmpDist --workpath $work $SpecFile
$buildOk = ($LASTEXITCODE -eq 0)
Remove-Item Env:\AGENTEBLUE_ONEFILE -ErrorAction SilentlyContinue
if (-not $buildOk) {
    Write-Host " [ERRO] Falha na compilacao do PyInstaller!" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $DistDir)) { New-Item -ItemType Directory -Path $DistDir | Out-Null }

if ($Onefile) {
    # ── ONEFILE: publica um unico .exe em dist\AgenteBlue.exe ────────────
    $builtExe = Join-Path $tmpDist "$AppName.exe"
    if (-not (Test-Path $builtExe)) {
        Write-Host " [ERRO] Executavel nao encontrado em $builtExe!" -ForegroundColor Red
        exit 1
    }
    Write-Step '3/4' "Publicando arquivo unico em $OnefileExe..."
    try {
        Copy-Item $builtExe -Destination $OnefileExe -Force -ErrorAction Stop
    } catch {
        Write-Host " [ERRO] Nao foi possivel substituir $OnefileExe (feche o Agente Blue)." -ForegroundColor Red
        exit 1
    }
    Write-Step '4/4' 'Copiando os agentes de monitoramento...'
    Copy-Agents $DistDir
    $resultExe = $OnefileExe
} else {
    # ── ONEDIR: publica a pasta unica dist\AgenteBlue\ ───────────────────
    $built = Join-Path $tmpDist $AppName
    if (-not (Test-Path (Join-Path $built "$AppName.exe"))) {
        Write-Host " [ERRO] Executavel nao encontrado em $built!" -ForegroundColor Red
        exit 1
    }
    Write-Step '3/4' "Publicando pasta em $OutDir..."
    if (Test-Path $OutDir) {
        try {
            Remove-Item -Recurse -Force $OutDir -ErrorAction Stop
        } catch {
            Write-Host " [ERRO] Nao foi possivel substituir $OutDir." -ForegroundColor Red
            Write-Host "        Feche o Agente Blue (e o Explorer aberto na pasta) e rode de novo." -ForegroundColor Red
            exit 1
        }
    }
    Move-Item -Path $built -Destination $OutDir -Force
    Write-Step '4/4' 'Copiando assets externos...'
    Copy-Assets
    Copy-Agents $OutDir
    $resultExe = $ExePath
}

# ── Limpeza dos temporários ─────────────────────────────────────────────
Remove-Item -Recurse -Force $work, $tmpDist -ErrorAction SilentlyContinue

# ── Resultado ───────────────────────────────────────────────────────────
$exe = Get-Item $resultExe
$mb  = [math]::Round($exe.Length / 1MB, 1)
Write-Host ""
Write-Host " ===================================================" -ForegroundColor Green
Write-Host "   BUILD CONCLUIDO  -  $modo" -ForegroundColor Green
Write-Host "   Exe:  $($exe.FullName)" -ForegroundColor Green
Write-Host "   Tam:  $mb MB    Data: $($exe.LastWriteTime)" -ForegroundColor Green
Write-Host " ===================================================" -ForegroundColor Green
Write-Host ""
if ($Onefile) {
    Write-Host " Entrega: copie este .exe + o config.json preenchido para a mesma pasta." -ForegroundColor DarkGray
    Write-Host "   (web, assets e installers ja vao embutidos; config.json fica de fora por conter senhas.)" -ForegroundColor DarkGray
    if ($env:AGENTEBLUE_EMBED_AGENTS -ne '1') {
        Write-Host "   Leve tambem a pasta dist\agents\ ao lado do exe -- e de onde a aba" -ForegroundColor DarkGray
        Write-Host "   Monitoramento instala o agente de metricas e o acesso remoto." -ForegroundColor DarkGray
    }
} else {
    Write-Host " Dica: alterou so a interface (html/js/css)? Use:" -ForegroundColor DarkGray
    Write-Host "   .\build\compilar.ps1 -AssetsOnly   (atualiza sem recompilar)" -ForegroundColor DarkGray
}
Write-Host ""
