param(
    [string]$IpLocal,
    [string]$UsuarioAdmin = "Administrador"
)

$IpLocal = "$IpLocal".Trim()

function Testar-Ping {
    param([string]$Ip)

    if (-not $Ip) { return $false }

    cmd /c "ping -n 1 -w 1000 $Ip" | Out-Null
    return ($LASTEXITCODE -eq 0)
}

$nova_senha_random = "ComplexA#$(Get-Random -Minimum 1000 -Maximum 9999)!"

Clear-Host
Write-Output "INICIANDO BLOQUEIO"
Write-Output "LOCAL: [$IpLocal]"

# Autoriza os IPs no TrustedHosts
Set-Item WSMan:\localhost\Client\TrustedHosts -Value "$IpLocal" -Force

# Solicita credencial de admin
$senha_admin = ConvertTo-SecureString "Sham23*" -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($UsuarioAdmin, $senha_admin)

function Executar-BloqueioRemoto {
    param(
        [string]$AlvoIp,
        [pscredential]$Credencial,
        [string]$SenhaNova
    )

    Invoke-Command -ComputerName $AlvoIp -Credential $Credencial -Authentication Negotiate -ScriptBlock {
        param($senha)

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
        netsh advfirewall firewall add rule name="WinRM BlueControl" protocol=TCP dir=in localport=5985 action=allow remoteip=192.168.1.151 | Out-Null
        Set-Service WinRM -StartupType Automatic | Out-Null
        Write-Output "WINRM_LIBERADO"
        Write-Output "REINICIANDO"
        shutdown /r /f /t 5
    } -ArgumentList $SenhaNova -ErrorAction Stop
}

Write-Output "PROCURANDO_ALVO"

# --- 1. TENTA LOCAL ---
if ($IpLocal) {
    $pingLocal = Testar-Ping -Ip $IpLocal

    if ($pingLocal) {
        Write-Output "ALVO_ENCONTRADO_LOCAL:$IpLocal"

        try {
            Write-Output "EXECUTANDO_BLOQUEIO:local"
            Executar-BloqueioRemoto -AlvoIp $IpLocal -Credencial $cred -SenhaNova $nova_senha_random
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