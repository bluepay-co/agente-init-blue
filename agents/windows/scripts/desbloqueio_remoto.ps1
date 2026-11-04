param(
    [string]$IpLocal,
    [string]$UsuarioAdmin = "Administrador",
    [string]$SenhaAdmin,
    # Senha que os usuarios reativados recebem. Sem padrao: uma senha fixa aqui
    # seria a mesma em todo equipamento desbloqueado da empresa.
    [string]$SenhaTemporaria
)

$IpLocal = "$IpLocal".Trim()

function Testar-Ping {
    param([string]$Ip)
    if (-not $Ip) { return $false }
    cmd /c "ping -n 1 -w 1000 $Ip" | Out-Null
    return ($LASTEXITCODE -eq 0)
}

Clear-Host
Write-Output "INICIANDO DESBLOQUEIO"
Write-Output "LOCAL: [$IpLocal]"

Set-Item WSMan:\localhost\Client\TrustedHosts -Value "$IpLocal" -Force

# Credencial do administrador local da maquina alvo. Sem padrao no codigo: uma
# senha versionada no Git vale tanto quanto nenhuma. Vem por -SenhaAdmin ou pela
# variavel de ambiente BLUECONTROL_ADMIN_PASS.
if (-not $SenhaAdmin) { $SenhaAdmin = $env:BLUECONTROL_ADMIN_PASS }
if ([string]::IsNullOrWhiteSpace($SenhaAdmin)) {
    Write-Output "ERRO_SEM_CREDENCIAL"
    Write-Output "Defina -SenhaAdmin ou a variavel de ambiente BLUECONTROL_ADMIN_PASS."
    exit 3
}

# Sem senha temporaria nao da pra reativar ninguem de forma util: o tecnico
# precisa saber qual e. Melhor parar aqui do que usar uma senha previsivel.
if ([string]::IsNullOrWhiteSpace($SenhaTemporaria)) {
    $SenhaTemporaria = $env:BLUECONTROL_SENHA_TEMP
}
if ([string]::IsNullOrWhiteSpace($SenhaTemporaria)) {
    Write-Output "ERRO_SEM_SENHA_TEMPORARIA"
    Write-Output "Defina -SenhaTemporaria ou a variavel BLUECONTROL_SENHA_TEMP."
    exit 4
}

$senha_admin = ConvertTo-SecureString $SenhaAdmin -AsPlainText -Force
# $UsuarioAdmin, nao "Administrador" fixo: o parametro existia e era ignorado,
# entao passar -UsuarioAdmin nao tinha efeito nenhum.
$cred = New-Object System.Management.Automation.PSCredential($UsuarioAdmin, $senha_admin)

function Executar-DesbloqueioRemoto {
    param(
        [string]$AlvoIp,
        [pscredential]$Credencial,
        [string]$SenhaNova
    )

    $SessionOptions = New-PSSessionOption -IdleTimeout 60000 -OpenTimeout 15000 -CancelTimeout 15000

    Invoke-Command -ComputerName $AlvoIp -Credential $Credencial -Authentication Negotiate -SessionOption $SessionOptions -ScriptBlock {
        param($nova_senha)

        $usuarios = Get-LocalUser | Where-Object {
            $_.Name -notmatch "Administrador|Administrator|Guest|Convidado|DefaultAccount|WDAGUtilityAccount|BluePay"
        }

        foreach ($u in $usuarios) {
            $nome = $u.Name
            Write-Output "REATIVANDO:$nome"
            $u | Enable-LocalUser -ErrorAction SilentlyContinue
            net user "$nome" /active:yes
            $u | Set-LocalUser -Password (ConvertTo-SecureString $nova_senha -AsPlainText -Force) -ErrorAction SilentlyContinue
        }

        $RegPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
        Set-ItemProperty -Path $RegPath -Name "legalnoticecaption" -Value ""
        Set-ItemProperty -Path $RegPath -Name "legalnoticetext" -Value ""
        Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -Name "CachedLogonsCount" -Value 10

        Write-Output "REINICIANDO"
        shutdown /r /f /t 5
    } -ArgumentList $SenhaNova -ErrorAction Stop
}

Write-Output "PROCURANDO_ALVO"

if ($IpLocal) {
    $pingLocal = Testar-Ping -Ip $IpLocal
    if ($pingLocal) {
        Write-Output "ALVO_ENCONTRADO_LOCAL:$IpLocal"
        try {
            Write-Output "EXECUTANDO_DESBLOQUEIO:local"
            Executar-DesbloqueioRemoto -AlvoIp $IpLocal -Credencial $cred -SenhaNova $SenhaTemporaria
            Write-Output "SUCESSO_DESBLOQUEIO:local"
            exit 0
        } catch {
            Write-Output "FALHA_LOCAL:$($_.Exception.Message)"
        }
    }
}

Write-Output "ALVO_OFFLINE"
exit 2