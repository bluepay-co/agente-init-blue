param(
    [string]$IpVpn,
    [string]$IpLocal,
    [string]$UsuarioAdmin    = "Administrador",
    [string]$SenhaTemporaria = "Suporte@123"
)

$IpVpn   = "$IpVpn".Trim()
$IpLocal = "$IpLocal".Trim()

function Testar-Ping {
    param([string]$Ip)
    if (-not $Ip) { return $false }
    cmd /c "ping -n 1 -w 1000 $Ip" | Out-Null
    return ($LASTEXITCODE -eq 0)
}

Clear-Host
Write-Output "INICIANDO DESBLOQUEIO"
Write-Output "VPN: [$IpVpn]"
Write-Output "LOCAL: [$IpLocal]"

Set-Item WSMan:\localhost\Client\TrustedHosts -Value "$IpVpn,$IpLocal" -Force

$senha_admin = ConvertTo-SecureString "Sham23*" -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential("Administrador", $senha_admin)

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

if ($IpVpn) {
    $pingVpn = Testar-Ping -Ip $IpVpn
    if ($pingVpn) {
        Write-Output "ALVO_ENCONTRADO_VPN:$IpVpn"
        try {
            Write-Output "EXECUTANDO_DESBLOQUEIO:vpn"
            Executar-DesbloqueioRemoto -AlvoIp $IpVpn -Credencial $cred -SenhaNova $SenhaTemporaria
            Write-Output "SUCESSO_DESBLOQUEIO:vpn"
            exit 0
        } catch {
            Write-Output "FALHA_VPN:$($_.Exception.Message)"
        }
    }
}

Write-Output "ALVO_OFFLINE"
exit 2