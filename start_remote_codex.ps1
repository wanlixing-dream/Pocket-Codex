param(
    [switch]$InstallWatchdog,
    [switch]$RemoveWatchdog
)

$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = 'RemoteCodexWatchdog'

if ($InstallWatchdog) {
    $taskExe = Join-Path $env:SystemRoot 'System32\schtasks.exe'
    $powerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $action = '"{0}" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{1}"' -f $powerShellExe,$MyInvocation.MyCommand.Path
    & $taskExe /Create /F /TN $TaskName /SC MINUTE /MO 5 /TR $action /RL LIMITED | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to create $TaskName." }
    $task = Get-ScheduledTask -TaskName $TaskName
    $settings = $task.Settings
    $settings.DisallowStartIfOnBatteries = $false
    $settings.StopIfGoingOnBatteries = $false
    Set-ScheduledTask -TaskName $TaskName -Settings $settings | Out-Null
    Write-Host "$TaskName installed. It will run every five minutes."
    exit 0
}

if ($RemoveWatchdog) {
    $taskExe = Join-Path $env:SystemRoot 'System32\schtasks.exe'
    & $taskExe /Delete /F /TN $TaskName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to remove $TaskName." }
    Write-Host "$TaskName removed."
    exit 0
}

$LogDir = Join-Path $env:LOCALAPPDATA 'RemoteCodex'
$LogFile = Join-Path $LogDir 'server.log'
$TunnelLog = Join-Path $LogDir 'tunnel.log'
$TunnelErrorLog = Join-Path $LogDir 'tunnel-error.log'
$UrlFile = Join-Path $LogDir 'remote-url.txt'
$Cloudflared = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like '*remote_codex_server.py*' -and $_.ProcessId -ne $PID
}
if (-not $existing) {
    Start-Process -FilePath 'pythonw.exe' `
        -ArgumentList ('"{0}"' -f (Join-Path $ProjectDir 'remote_codex_server.py')) `
        -WorkingDirectory $ProjectDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $LogFile `
        -RedirectStandardError (Join-Path $LogDir 'server-error.log')
}

if (-not (Test-Path $Cloudflared)) {
    exit 0
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

if (-not $publicUrl) {
    exit 0
}

$token = (Get-Content (Join-Path $ProjectDir 'remote.env') | Where-Object {
    $_ -like 'REMOTE_CODEX_TOKEN=*'
} | Select-Object -First 1).Split('=', 2)[1]
$mobileUrl = "$publicUrl/#token=$token"
Set-Content -Path $UrlFile -Value $mobileUrl -Encoding ASCII

$lastNotifiedFile = Join-Path $LogDir 'last-notified-url.txt'
$lastNotified = Get-Content $lastNotifiedFile -Raw -ErrorAction SilentlyContinue
if ($lastNotified -and $lastNotified.Trim() -eq $mobileUrl) {
    exit 0
}

$watchEnv = @{}
Get-Content (Join-Path $ProjectDir 'watch.env') | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
        $parts = $line.Split('=', 2)
        $watchEnv[$parts[0].Trim()] = $parts[1].Trim()
    }
}
$topic = $watchEnv['NTFY_NOTIFY_TOPIC']
if (-not $topic) { exit 0 }
$base = if ($watchEnv['NTFY_BASE']) { $watchEnv['NTFY_BASE'].TrimEnd('/') } else { 'https://ntfy.sh' }
$headers = @{ 'Content-Type' = 'application/json' }
if ($watchEnv['NTFY_TOKEN']) {
    $headers['Authorization'] = "Bearer $($watchEnv['NTFY_TOKEN'])"
}
$payload = @{
    'topic' = $topic
    'title' = 'Codex Remote - NEW LINK'
    'message' = "Open the current Codex remote page:`n$mobileUrl"
    'priority' = 5
    'click' = $mobileUrl
    'tags' = @('computer')
    'actions' = @(
        @{
            'action' = 'view'
            'label' = 'OPEN CODEX'
            'url' = $mobileUrl
            'clear' = $true
        }
    )
}
try {
    Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$base/" `
        -Headers $headers -Body ([Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 5 -Compress))) `
        -TimeoutSec 15 | Out-Null
    Set-Content -Path $lastNotifiedFile -Value $mobileUrl -Encoding ASCII
} catch {
    $_ | Out-File (Join-Path $LogDir 'notify-error.log') -Encoding UTF8
}
