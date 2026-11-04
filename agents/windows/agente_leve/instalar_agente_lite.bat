@echo off
setlocal enabledelayedexpansion

REM ════════════════════════════════════════════════════════════════════════════
REM BlueControl IT — Instalador do Agente Leve v1
REM ════════════════════════════════════════════════════════════════════════════

title BlueControl Lite - Instalador
color 0A

echo.
echo ════════════════════════════════════════════════════════════════════════════
echo  BlueControl IT - Agente de Monitoramento Leve
echo ════════════════════════════════════════════════════════════════════════════
echo.

REM Verifica se está rodando como Admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Este script precisa ser executado como Administrador!
    echo.
    echo Clique com botão direito no arquivo e escolha "Executar como administrador"
    pause
    exit /b 1
)

echo [✓] Executando como Administrador
echo.

REM O token não tem padrão no código: defina BLUECONTROL_AGENT_TOKEN antes de rodar
if "%BLUECONTROL_AGENT_TOKEN%"=="" (
    echo [ERRO] Variável BLUECONTROL_AGENT_TOKEN não definida.
    echo        Execute:  set BLUECONTROL_AGENT_TOKEN=^<token^>
    pause
    exit /b 1
)

REM ────────────────────────────────────────────────────────────────────────────
REM 1. CRIAR PASTA
REM ────────────────────────────────────────────────────────────────────────────
echo [1/6] Criando pasta C:\BlueControl...
if not exist "C:\BlueControl" (
    mkdir "C:\BlueControl"
    echo [✓] Pasta criada
) else (
    echo [✓] Pasta já existe
)

REM ────────────────────────────────────────────────────────────────────────────
REM 2. COPIAR ARQUIVOS
REM ────────────────────────────────────────────────────────────────────────────
echo.
echo [2/6] Copiando arquivos...

if exist "agente_monitoramento_leve.py" (
    copy "agente_monitoramento_leve.py" "C:\BlueControl\" >nul
    echo [✓] agente_monitoramento_leve.py copiado
) else (
    echo [!] Aviso: agente_monitoramento_leve.py não encontrado na pasta de instalação
)

REM Criar config.json se não existir
if not exist "C:\BlueControl\config.json" (
    echo [✓] Criando config.json padrão...
    (
        echo {
        echo   "patrimonio": "0001",
        echo   "url_api": "http://192.168.10.11:8550",
        echo   "token_agente": "%BLUECONTROL_AGENT_TOKEN%",
        echo   "intervalo_segundos": 300
        echo }
    ) > "C:\BlueControl\config.json"
    echo [✓] config.json criado
) else (
    echo [✓] config.json já existe (não substituindo)
)

REM ────────────────────────────────────────────────────────────────────────────
REM 3. VERIFICAR/INSTALAR PYTHON
REM ────────────────────────────────────────────────────────────────────────────
echo.
echo [3/6] Verificando Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python não encontrado no PATH
    echo.
    echo Opções:
    echo 1. Instalar Python via Windows Store (recomendado)
    echo 2. Baixar em https://www.python.org/downloads/
    echo.
    pause
) else (
    for /f "tokens=*" %%A in ('python --version 2^>^&1') do set PYTHON_VERSION=%%A
    echo [✓] !PYTHON_VERSION! encontrado
)

REM ────────────────────────────────────────────────────────────────────────────
REM 4. INSTALAR DEPENDÊNCIAS
REM ────────────────────────────────────────────────────────────────────────────
echo.
echo [4/6] Instalando dependências (psutil, requests)...
python -m pip install --quiet --upgrade pip >nul 2>&1
python -m pip install --quiet psutil requests >nul 2>&1
if %errorlevel% equ 0 (
    echo [✓] Dependências instaladas
) else (
    echo [!] Erro ao instalar dependências
    pause
    exit /b 1
)

REM ────────────────────────────────────────────────────────────────────────────
REM 5. COMPILAR EXE (Opcional - se PyInstaller disponível)
REM ────────────────────────────────────────────────────────────────────────────
echo.
echo [5/6] Compilando executável (opcional)...
python -m pip install --quiet pyinstaller >nul 2>&1

if exist "C:\BlueControl\agente_monitoramento_leve.py" (
    echo Compilando... (pode levar 30-60 segundos)
    cd /d "C:\BlueControl"
    pyinstaller --onefile --noconsole ^
        --name "BlueControl_Lite" ^
        --distpath "." ^
        --specpath ".build" ^
        --buildpath ".build" ^
        "agente_monitoramento_leve.py" >nul 2>&1

    if exist "C:\BlueControl\BlueControl_Lite.exe" (
        echo [✓] Executável compilado: BlueControl_Lite.exe
    ) else (
        echo [!] Não foi possível compilar, mas o agente pode rodar via Python direto
    )
) else (
    echo [!] Arquivo não encontrado para compilar
)

REM ────────────────────────────────────────────────────────────────────────────
REM 6. INSTALAR COMO SERVIÇO WINDOWS
REM ────────────────────────────────────────────────────────────────────────────
echo.
echo [6/6] Instalando Serviço Windows...

REM Primeiro, remove serviço antigo se existir
sc query "BlueControlLite" >nul 2>&1
if %errorlevel% equ 0 (
    echo Parando serviço antigo...
    net stop "BlueControlLite" >nul 2>&1
    ping 127.0.0.1 -n 2 >nul 2>&1
)

REM Tenta usar o EXE compilado primeiro, depois fallback para Python
if exist "C:\BlueControl\BlueControl_Lite.exe" (
    echo Instalando serviço com executável compilado...
    sc create "BlueControlLite" ^
        displayname="BlueControl IT - Agente Leve" ^
        binpath= "C:\BlueControl\BlueControl_Lite.exe" ^
        start= "auto" >nul 2>&1
    echo [✓] Serviço criado (via EXE)
) else (
    echo Instalando serviço com Python...
    sc create "BlueControlLite" ^
        displayname="BlueControl IT - Agente Leve" ^
        binpath= "python C:\BlueControl\agente_monitoramento_leve.py install" ^
        start= "auto" >nul 2>&1
    echo [✓] Serviço criado (via Python)
)

REM Inicia o serviço
echo.
echo Iniciando serviço...
net start "BlueControlLite" >nul 2>&1
if %errorlevel% equ 0 (
    echo [✓] Serviço iniciado com sucesso!
) else (
    echo [!] Erro ao iniciar serviço, mas pode estar configurado
)

REM ────────────────────────────────────────────────────────────────────────────
REM RESUMO FINAL
REM ────────────────────────────────────────────────────────────────────────────
echo.
echo ════════════════════════════════════════════════════════════════════════════
echo  ✓ Instalação Concluída!
echo ════════════════════════════════════════════════════════════════════════════
echo.
echo Arquivo de Configuração: C:\BlueControl\config.json
echo Log do Agente: C:\BlueControl\lite.log
echo.
echo Próximos passos:
echo 1. Edite C:\BlueControl\config.json com os dados corretos:
echo    - patrimonio: número do equipamento
echo    - url_api: endereço da API BlueControl
echo    - token_agente: token de autenticação
echo.
echo 2. Reinicie o serviço:
echo    net stop BlueControlLite
echo    net start BlueControlLite
echo.
echo 3. Verifique o log:
echo    type C:\BlueControl\lite.log
echo.
echo Para parar o serviço:
echo    net stop BlueControlLite
echo.
echo Para desinstalar:
echo    sc delete BlueControlLite
echo.
pause
