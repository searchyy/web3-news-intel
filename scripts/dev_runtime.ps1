param(
    [switch]$Status,
    [switch]$Start,
    [switch]$Stop
)

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RuntimeDir = Join-Path $ProjectRoot '.runtime'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$EnvFile = Join-Path $ProjectRoot '.env'

$Processes = @(
    @{
        Name = 'ai-worker'
        Args = @('-m', 'celery', '-A', 'app.workers.celery_app', 'worker', '--hostname=ai-worker@%h', '--loglevel=INFO', '--queues=ai')
    },
    @{
        Name = 'publisher-worker'
        Args = @('-m', 'celery', '-A', 'app.workers.celery_app', 'worker', '--hostname=publisher-worker@%h', '--loglevel=INFO', '--queues=web3-news-intel')
    },
    @{
        Name = 'scheduler'
        Args = @('-m', 'celery', '-A', 'app.workers.celery_app', 'beat', '--loglevel=INFO', "--schedule=$RuntimeDir\celerybeat-schedule")
    }
)

function Write-Info($Message) {
    Write-Host "[web3-news-intel] $Message"
}

function Import-DotEnv {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        return
    }
    foreach ($line in Get-Content -Encoding UTF8 -LiteralPath $EnvFile) {
        if ($line -notmatch '^\s*([^#=]+)\s*=\s*(.*)\s*$') {
            continue
        }
        $key = $Matches[1].Trim()
        $value = $Matches[2].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($key, $value, 'Process')
    }
}

function Get-BrokerUrl {
    if ($env:CELERY_BROKER_URL) {
        return $env:CELERY_BROKER_URL
    }
    if ($env:REDIS_URL) {
        return $env:REDIS_URL
    }
    return 'redis://127.0.0.1:6379/0'
}

function Test-Redis {
    $url = Get-BrokerUrl
    try {
        $uri = [Uri]$url
        $hostName = $uri.Host
        $port = if ($uri.Port -gt 0) { $uri.Port } else { 6379 }
        $client = [System.Net.Sockets.TcpClient]::new()
        $task = $client.ConnectAsync($hostName, $port)
        if (-not $task.Wait(1500)) {
            $client.Dispose()
            return @{ Ok = $false; Url = $url; Detail = "连接超时" }
        }
        $client.Dispose()
        return @{ Ok = $true; Url = $url; Detail = "可连接" }
    } catch {
        return @{ Ok = $false; Url = $url; Detail = $_.Exception.Message }
    }
}

function Get-PidFile($Name) {
    return Join-Path $RuntimeDir "$Name.pid"
}

function Get-LogFile($Name, $Kind) {
    return Join-Path $RuntimeDir "$Name.$Kind.log"
}

function Get-ManagedProcess($Name) {
    $pidFile = Get-PidFile $Name
    if (-not (Test-Path -LiteralPath $pidFile)) {
        return $null
    }
    $raw = (Get-Content -LiteralPath $pidFile -TotalCount 1).Trim()
    if (-not ($raw -as [int])) {
        return $null
    }
    $processId = [int]$raw
    return Get-Process -Id $processId -ErrorAction SilentlyContinue
}

function Start-ManagedProcess($Spec) {
    $name = $Spec.Name
    $existing = Get-ManagedProcess $name
    if ($existing) {
        Write-Info "$name 已在运行，PID=$($existing.Id)"
        return
    }
    $stdout = Get-LogFile $name 'out'
    $stderr = Get-LogFile $name 'err'
    $process = Start-Process -FilePath $Python `
        -ArgumentList $Spec.Args `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -Encoding UTF8 -LiteralPath (Get-PidFile $name) -Value ([string]$process.Id)
    Write-Info "$name 已启动，PID=$($process.Id)，日志：$stdout / $stderr"
}

function Stop-ManagedProcess($Name) {
    $process = Get-ManagedProcess $Name
    if (-not $process) {
        Write-Info "$Name 未运行"
        return
    }
    Stop-Process -Id $process.Id
    Remove-Item -LiteralPath (Get-PidFile $Name) -Force -ErrorAction SilentlyContinue
    Write-Info "$Name 已停止，PID=$($process.Id)"
}

function Show-Status {
    $redis = Test-Redis
    $redisStatus = if ($redis.Ok) { '正常' } else { "不可用：$($redis.Detail)" }
    Write-Info "Redis：$redisStatus ($($redis.Url))"
    foreach ($spec in $Processes) {
        $name = $spec.Name
        $process = Get-ManagedProcess $name
        $stdout = Get-LogFile $name 'out'
        $stderr = Get-LogFile $name 'err'
        if ($process) {
            Write-Info "$name：运行中，PID=$($process.Id)，日志：$stdout / $stderr"
        } else {
            Write-Info "$name：未运行，日志：$stdout / $stderr"
        }
    }
}

$selected = @($Status, $Start, $Stop).Where({ $_ }).Count
if ($selected -ne 1) {
    Write-Error "请指定且只指定一个参数：-Status、-Start 或 -Stop"
}
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "未找到项目虚拟环境 Python：$Python"
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
Import-DotEnv

if ($Status) {
    Show-Status
    exit 0
}

if ($Stop) {
    $toStop = @($Processes)
    [array]::Reverse($toStop)
    foreach ($spec in $toStop) {
        Stop-ManagedProcess $spec.Name
    }
    Show-Status
    exit 0
}

$redis = Test-Redis
if (-not $redis.Ok) {
    Write-Info "Redis 不可用：$($redis.Detail)"
    Write-Info "当前 Broker：$($redis.Url)"
    Write-Info "请先启动 Redis，并确认 REDIS_URL/CELERY_BROKER_URL 指向可连接地址。"
    exit 2
}

foreach ($spec in $Processes) {
    Start-ManagedProcess $spec
}
Show-Status
