param(
    [switch]$InstallWatchdog,
    [switch]$RemoveWatchdog,
    [ValidateSet('Cloudflare', 'Tailscale')]
    [string]$AccessMode,
    [switch]$WatchdogRun,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
$ScriptPath = $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = 'RemoteCodexWatchdog'
$LocalUrl = 'http://127.0.0.1:8765'
$LogDir = Join-Path $env:LOCALAPPDATA 'RemoteCodex'
$LogFile = Join-Path $LogDir 'server.log'
$ServerErrorLog = Join-Path $LogDir 'server-error.log'
$TunnelLog = Join-Path $LogDir 'tunnel.log'
$TunnelErrorLog = Join-Path $LogDir 'tunnel-error.log'
$UrlFile = Join-Path $LogDir 'remote-url.txt'
$ModeFile = Join-Path $LogDir 'access-mode.txt'
$LastNotifiedFile = Join-Path $LogDir 'last-notified-url.txt'
$WatchdogErrorLog = Join-Path $LogDir 'watchdog-error.log'
$Cloudflared = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Read-KeyValueFile([string]$Path) {
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $parts = $line.Split('=', 2)
            $values[$parts[0].Trim()] = $parts[1].Trim()
        }
    }
    return $values
}

function Protect-PrivateFile([string]$Path) {
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $icacls = Join-Path $env:SystemRoot 'System32\icacls.exe'
    & $icacls $Path /inheritance:r /grant:r "*$($sid):(F)" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to restrict access to $Path."
    }
}

function Resolve-AccessMode {
    if ($AccessMode) {
        return $AccessMode
    }
    if (Test-Path -LiteralPath $ModeFile) {
        $saved = (Get-Content -LiteralPath $ModeFile -Raw).Trim()
        if ($saved -in @('Cloudflare', 'Tailscale')) {
            return $saved
        }
    }
    return 'Cloudflare'
}

function Install-Watchdog([string]$Mode) {
    $taskExe = Join-Path $env:SystemRoot 'System32\schtasks.exe'
    $powerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $action = '"{0}" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{1}" -AccessMode {2} -WatchdogRun' -f $powerShellExe,$ScriptPath,$Mode
    & $taskExe /Create /F /TN $TaskName /SC MINUTE /MO 5 /TR $action /RL LIMITED | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to create $TaskName." }
    $task = Get-ScheduledTask -TaskName $TaskName
    $settings = $task.Settings
    $settings.DisallowStartIfOnBatteries = $false
    $settings.StopIfGoingOnBatteries = $false
    Set-ScheduledTask -TaskName $TaskName -Settings $settings | Out-Null
    Write-Host "$TaskName installed in $Mode mode. It will run every five minutes."
}

function Remove-Watchdog {
    $taskExe = Join-Path $env:SystemRoot 'System32\schtasks.exe'
    & $taskExe /Delete /F /TN $TaskName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to remove $TaskName." }
    Write-Host "$TaskName removed. Existing Serve or tunnel configuration was left unchanged."
}

function Start-PocketCodex {
    $serverPath = Join-Path $ProjectDir 'remote_codex_server.py'
    foreach ($privateLog in @($LogFile, $ServerErrorLog)) {
        if (-not (Test-Path -LiteralPath $privateLog)) {
            New-Item -ItemType File -Path $privateLog -Force | Out-Null
        }
        Protect-PrivateFile $privateLog
    }
    $existing = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -like "*$serverPath*" -and $_.ProcessId -ne $PID
    }
    if (-not $existing) {
        $pythonw = (Get-Command 'pythonw.exe' -ErrorAction Stop).Source
        Start-Process -FilePath $pythonw `
            -ArgumentList ('"{0}"' -f $serverPath) `
            -WorkingDirectory $ProjectDir `
            -WindowStyle Hidden `
            -RedirectStandardOutput $LogFile `
            -RedirectStandardError $ServerErrorLog
    }

    $deadline = (Get-Date).AddSeconds(30)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$LocalUrl/health" -TimeoutSec 3
            if ($response.StatusCode -eq 200) { return }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)
    throw "PocketCodex did not become healthy at $LocalUrl. See $ServerErrorLog."
}

function Find-Tailscale {
    $command = Get-Command 'tailscale.exe' -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Tailscale\tailscale.exe')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    throw 'Tailscale is not installed. Install it from https://tailscale.com/download and sign in.'
}

function Invoke-Tailscale([string]$Executable, [string[]]$Arguments) {
    $output = (& $Executable @Arguments 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Tailscale command failed: $output"
    }
    return $output
}

function Get-TailscaleState([string]$Executable) {
    $raw = Invoke-Tailscale $Executable @('status', '--json')
    try {
        $state = $raw | ConvertFrom-Json
    } catch {
        throw 'Tailscale returned an invalid status response.'
    }
    if ($state.BackendState -ne 'Running') {
        throw 'Tailscale is not connected. Sign in on this computer and rerun setup.'
    }
    $dnsName = [string]$state.Self.DNSName
    if (-not $dnsName) {
        throw 'Tailscale did not provide a stable MagicDNS hostname.'
    }
    return [PSCustomObject]@{
        DnsName = $dnsName.TrimEnd('.')
        Online = [bool]$state.Self.Online
    }
}

function Get-RemoteToken {
    $envPath = Join-Path $ProjectDir 'remote.env'
    $config = Read-KeyValueFile $envPath
    $token = [string]$config['REMOTE_CODEX_TOKEN']
    if ($token.Length -lt 24) {
        throw 'remote.env is missing a valid REMOTE_CODEX_TOKEN.'
    }
    Protect-PrivateFile $envPath
    return $token
}

function Test-TailscaleEndpoint([string]$BaseUrl, [string]$Token) {
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.UseProxy = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(10)
    try {
        $deadline = (Get-Date).AddSeconds(60)
        do {
            try {
                $root = $client.GetAsync("$BaseUrl/").GetAwaiter().GetResult()
                $request = New-Object System.Net.Http.HttpRequestMessage(
                    [System.Net.Http.HttpMethod]::Get,
                    "$BaseUrl/api/sessions"
                )
                $request.Headers.Add('X-Remote-Codex-Token', $Token)
                $sessions = $client.SendAsync($request).GetAwaiter().GetResult()
                if ($root.IsSuccessStatusCode -and $sessions.IsSuccessStatusCode) { return }
            } catch {
                # Certificate provisioning and MagicDNS may take a few seconds after first setup.
            }
            Start-Sleep -Seconds 2
        } while ((Get-Date) -lt $deadline)
    } finally {
        $client.Dispose()
        $handler.Dispose()
    }
    throw 'The Tailscale HTTPS endpoint did not pass its page and authenticated API checks.'
}

function Stop-CloudflareTunnel {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'cloudflared.exe' -and $_.CommandLine -like '*127.0.0.1:8765*'
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force
    }
}

function Save-MobileUrl([string]$MobileUrl) {
    Set-Content -LiteralPath $UrlFile -Value $MobileUrl -Encoding ASCII
    Protect-PrivateFile $UrlFile
}

function Notify-MobileUrl([string]$MobileUrl, [string]$Title) {
    $watchEnvPath = Join-Path $ProjectDir 'watch.env'
    if (Test-Path -LiteralPath $watchEnvPath) {
        Protect-PrivateFile $watchEnvPath
    }
    $lastNotified = Get-Content -LiteralPath $LastNotifiedFile -Raw -ErrorAction SilentlyContinue
    if ($lastNotified -and $lastNotified.Trim() -eq $MobileUrl) { return }

    $watchEnv = Read-KeyValueFile $watchEnvPath
    $topic = [string]$watchEnv['NTFY_NOTIFY_TOPIC']
    if (-not $topic) { return }
    $base = if ($watchEnv['NTFY_BASE']) { $watchEnv['NTFY_BASE'].TrimEnd('/') } else { 'https://ntfy.sh' }
    $headers = @{ 'Content-Type' = 'application/json' }
    if ($watchEnv['NTFY_TOKEN']) {
        $headers['Authorization'] = "Bearer $($watchEnv['NTFY_TOKEN'])"
    }
    $payload = @{
        'topic' = $topic
        'title' = $Title
        'message' = "Open the Codex remote page:`n$MobileUrl"
        'priority' = 5
        'click' = $MobileUrl
        'tags' = @('computer')
        'actions' = @(
            @{
                'action' = 'view'
                'label' = 'OPEN CODEX'
                'url' = $MobileUrl
                'clear' = $true
            }
        )
    }
    try {
        Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$base/" `
            -Headers $headers `
            -Body ([Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 5 -Compress))) `
            -TimeoutSec 15 | Out-Null
        Set-Content -LiteralPath $LastNotifiedFile -Value $MobileUrl -Encoding ASCII
        Protect-PrivateFile $LastNotifiedFile
    } catch {
        $_ | Out-File (Join-Path $LogDir 'notify-error.log') -Encoding UTF8
    }
}

function Start-TailscaleAccess {
    $tailscale = Find-Tailscale
    $state = Get-TailscaleState $tailscale
    Start-PocketCodex
    Invoke-Tailscale $tailscale @('serve', '--bg', '--yes', $LocalUrl) | Out-Null
    $state = Get-TailscaleState $tailscale
    $baseUrl = "https://$($state.DnsName)"
    $token = Get-RemoteToken
    Test-TailscaleEndpoint $baseUrl $token
    $mobileUrl = "$baseUrl/#token=$token"
    Save-MobileUrl $mobileUrl
    Stop-CloudflareTunnel
    Notify-MobileUrl $mobileUrl 'Codex Remote - FIXED LINK'
    Write-Host "Tailscale access is healthy at $baseUrl."
    Write-Host "The private tokenized link is stored in $UrlFile."
}

function Start-CloudflareAccess {
    Start-PocketCodex
    if (-not (Test-Path -LiteralPath $Cloudflared)) {
        throw "cloudflared was not found at $Cloudflared."
    }

    $tunnel = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'cloudflared.exe' -and $_.CommandLine -like '*127.0.0.1:8765*'
    }
    if ($tunnel) {
        $existingTunnelText = Get-Content $TunnelLog,$TunnelErrorLog -Raw -ErrorAction SilentlyContinue
        $lastConnected = $existingTunnelText.LastIndexOf('Registered tunnel connection')
        $lastMissing = $existingTunnelText.LastIndexOf('Unauthorized: Tunnel not found')
        if ($lastMissing -gt $lastConnected) {
            $tunnel | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
            Start-Sleep -Seconds 1
            $tunnel = $null
        }
    }
    if (-not $tunnel) {
        Remove-Item $TunnelLog,$TunnelErrorLog -Force -ErrorAction SilentlyContinue
        Start-Process -FilePath $Cloudflared `
            -ArgumentList 'tunnel --url http://127.0.0.1:8765 --protocol http2 --no-autoupdate' `
            -WindowStyle Hidden `
            -RedirectStandardOutput $TunnelLog `
            -RedirectStandardError $TunnelErrorLog
    }

    $deadline = (Get-Date).AddSeconds(45)
    $publicUrl = $null
    do {
        Start-Sleep -Seconds 1
        $tunnelText = Get-Content $TunnelLog,$TunnelErrorLog -Raw -ErrorAction SilentlyContinue
        $match = [regex]::Match($tunnelText, 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($match.Success) { $publicUrl = $match.Value }
    } while (-not $publicUrl -and (Get-Date) -lt $deadline)
    if (-not $publicUrl) { throw 'Cloudflare Quick Tunnel did not provide a public URL.' }

    $mobileUrl = "$publicUrl/#token=$(Get-RemoteToken)"
    Save-MobileUrl $mobileUrl
    Notify-MobileUrl $mobileUrl 'Codex Remote - NEW LINK'
    Write-Host "Cloudflare Quick Tunnel is available. The private link is stored in $UrlFile."
}

function Show-RemoteStatus([string]$Mode) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $taskState = if ($task) { [string]$task.State } else { 'Not installed' }
    try {
        $health = (Invoke-WebRequest -UseBasicParsing -Uri "$LocalUrl/health" -TimeoutSec 3).StatusCode
    } catch {
        $health = 'Unavailable'
    }
    Write-Host "Access mode: $Mode"
    Write-Host "PocketCodex health: $health"
    Write-Host "Watchdog: $taskState"
    if ($Mode -eq 'Tailscale') {
        $tailscale = Find-Tailscale
        $state = Get-TailscaleState $tailscale
        $serve = Invoke-Tailscale $tailscale @('serve', 'status')
        Write-Host "Tailscale: connected ($($state.DnsName))"
        Write-Host $serve
    }
}

try {
    if ($RemoveWatchdog) {
        Remove-Watchdog
        exit 0
    }

    $resolvedMode = Resolve-AccessMode
    if ($Status) {
        Show-RemoteStatus $resolvedMode
        exit 0
    }

    if ($resolvedMode -eq 'Tailscale' -and $WatchdogRun) {
        Start-PocketCodex
    } elseif ($resolvedMode -eq 'Tailscale') {
        Start-TailscaleAccess
    } else {
        Start-CloudflareAccess
    }

    Set-Content -LiteralPath $ModeFile -Value $resolvedMode -Encoding ASCII
    if ($InstallWatchdog) {
        Install-Watchdog $resolvedMode
    }
    Remove-Item -LiteralPath $WatchdogErrorLog -Force -ErrorAction SilentlyContinue
} catch {
    $message = '{0}: {1}' -f $_.Exception.GetType().FullName,$_.Exception.Message
    Set-Content -LiteralPath $WatchdogErrorLog -Value $message -Encoding UTF8
    throw
}
