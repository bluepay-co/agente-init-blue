param(
    [string]$IpLocal,
    [string]$UsuarioAdmin = "Administrador",
    [string]$SenhaAdmin,
    # Servidor do BlueControl IT: o unico que fica liberado no WinRM da maquina
    # bloqueada. Passe -IpServidor se o endereco mudar.
    [string]$IpServidor = "192.168.10.11"
)

$IpLocal = "$IpLocal".Trim()

function Testar-Ping {
    param([string]$Ip)

    if (-not $Ip) { return $false }

    cmd /c "ping -n 1 -w 1000 $Ip" | Out-Null
    return ($LASTEXITCODE -eq 0)
}

# Senha que trava o usuario para fora. Ninguem precisa conhece-la, entao e
# sorteada -- mas com entropia de verdade: o "Get-Random 1000..9999" de antes
# dava 9 mil possibilidades, chutaveis por quem quisesse voltar a entrar.
$bytes = New-Object byte[] 24
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$nova_senha_random = "Bl!" + [Convert]::ToBase64String($bytes)

Clear-Host
Write-Output "INICIANDO BLOQUEIO"
Write-Output "LOCAL: [$IpLocal]"

# Autoriza os IPs no TrustedHosts
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
$senha_admin = ConvertTo-SecureString $SenhaAdmin -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($UsuarioAdmin, $senha_admin)

function Executar-BloqueioRemoto {
    param(
        [string]$AlvoIp,
        [pscredential]$Credencial,
        [string]$SenhaNova,
        [string]$IpServidor
    )

    # O ScriptBlock roda na maquina remota e NAO ve as variaveis daqui: tudo o
    # que ele usa precisa entrar por -ArgumentList.
    Invoke-Command -ComputerName $AlvoIp -Credential $Credencial -Authentication Negotiate -ScriptBlock {
        param($senha, $ipServidor)

        $usuarios = Get-LocalUser | Where-Object {
            $_.Enabled -eq $true -and
            $_.Name -notmatch "Administrador|Administrator|Guest|Convidado|DefaultAccount|WDAGUtilityAccount|BluePay"
        }

        if ($usuarios -and $usuarios.Count -gt 0) {
            foreach ($u in $usuarios) {
                $nome = $u.Name
                Write-Output "NEUTRALIZANDO:$nome"

                $u | Set-LocalUser -Password (ConvertTo-SecureString $senha -AsPlainText -Force) -ErrorAction SilentlyContinue

                $sessoes = quser | Select-String $nome
                foreach ($s in $sessoes) {
                    $id = ($s.ToString() -split ' +')[2]
                    logoff $id
                }

                $u | Disable-LocalUser -ErrorAction SilentlyContinue
                net user "$nome" /active:no
            }
        }
        else {
            Write-Output "SEM_USUARIO_COMUM"
        }

        $RegPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
        Set-ItemProperty -Path $RegPath -Name "legalnoticecaption" -Value "ACESSO REVOGADO"
        Set-ItemProperty -Path $RegPath -Name "legalnoticetext" -Value "Este equipamento foi bloqueado pela TI. O seu acesso foi revogado. Favor devolver o equipamento imediatamente."

        Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -Name "CachedLogonsCount" -Value 0

        # Libera WinRM apenas para o servidor BlueControl
        Enable-PSRemoting -Force -SkipNetworkProfileCheck | Out-Null
        netsh advfirewall firewall add rule name="WinRM BlueControl" protocol=TCP dir=in localport=5985 action=allow remoteip=$ipServidor | Out-Null
        Set-Service WinRM -StartupType Automatic | Out-Null
        Write-Output "WINRM_LIBERADO"
        Write-Output "REINICIANDO"
        shutdown /r /f /t 5
    } -ArgumentList $SenhaNova, $IpServidor -ErrorAction Stop
}

Write-Output "PROCURANDO_ALVO"

# --- 1. TENTA LOCAL ---
if ($IpLocal) {
    $pingLocal = Testar-Ping -Ip $IpLocal

    if ($pingLocal) {
        Write-Output "ALVO_ENCONTRADO_LOCAL:$IpLocal"

        try {
            Write-Output "EXECUTANDO_BLOQUEIO:local"
            Executar-BloqueioRemoto -AlvoIp $IpLocal -Credencial $cred -SenhaNova $nova_senha_random -IpServidor $IpServidor
            Write-Output "SUCESSO_BLOQUEIO:local"
            exit 0
        }
        catch {
            Write-Output "FALHA_LOCAL"
        }
    }
}

# --- 2. SE NADA FUNCIONOU ---
Write-Output "ALVO_OFFLINE"
exit 2