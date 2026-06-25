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

function Normalize-ProcessPathEnv {
    $pathValue = [Environment]::GetEnvironmentVariable('PATH', 'Process')
    if (-not $pathValue) {
        $pathValue = [Environment]::GetEnvironmentVariable('Path', 'Process')
    }
    if ($pathValue) {
        [Environment]::SetEnvironmentVariable('PATH', $null, 'Process')
        [Environment]::SetEnvironmentVariable('Path', $pathValue, 'Process')
    }
}

Normalize-ProcessPathEnv

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RuntimeDir = Join-Path $ProjectRoot '.runtime'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$EnvFile = Join-Path $ProjectRoot '.env'
$DefaultDatabaseUrl = 'postgresql+psycopg://web3_news:web3_news@localhost:5432/web3_news_intel'

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

function Get-DatabaseUrl {
    if ($env:DATABASE_URL) {
        return $env:DATABASE_URL
    }
    return $DefaultDatabaseUrl
}

function Get-DisplayDatabaseUrl {
    $databaseUrl = Get-DatabaseUrl
    if ($databaseUrl -match '^(?<prefix>[^:]+://[^:/@]+):[^@]+@(?<suffix>.+)$') {
        return "$($Matches.prefix):***@$($Matches.suffix)"
    }
    return $databaseUrl
}

function Get-DatabaseKind {
    $databaseUrl = (Get-DatabaseUrl).Trim().ToLowerInvariant()
    if ($databaseUrl.StartsWith('sqlite')) {
        return 'sqlite'
    }
    if ($databaseUrl.StartsWith('postgresql') -or $databaseUrl.StartsWith('postgres://')) {
        return 'postgresql'
    }
    return 'other'
}

function Get-WorkerProfile {
    $kind = Get-DatabaseKind
    if ($kind -eq 'sqlite') {
        return @{
            DatabaseKind = $kind
            Name = 'sqlite-low-concurrency'
            AiPool = 'solo'
            AiConcurrency = 1
            ReportConcurrency = 1
            FetchConcurrency = 2
            PipelineConcurrency = 1
        }
    }
    if ($kind -eq 'postgresql') {
        return @{
            DatabaseKind = $kind
            Name = 'postgresql-standard'
            AiPool = 'threads'
            AiConcurrency = 2
            ReportConcurrency = 1
            FetchConcurrency = 8
            PipelineConcurrency = 4
        }
    }
    return @{
        DatabaseKind = $kind
        Name = 'unknown-db-low-concurrency'
        AiPool = 'solo'
        AiConcurrency = 1
        ReportConcurrency = 1
        FetchConcurrency = 1
        PipelineConcurrency = 1
    }
}

function New-CeleryWorkerSpec {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Queue,
        [Parameter(Mandatory = $true)][string]$Pool,
        [Parameter(Mandatory = $true)][int]$Concurrency
    )
    return @{
        Name = $Name
        Args = @(
            '-m', 'celery', '-A', 'app.workers.celery_app', 'worker',
            "--hostname=$Name@%h",
            '--loglevel=INFO',
            "--pool=$Pool",
            "--concurrency=$Concurrency",
            "--queues=$Queue"
        )
    }
}

function Get-ProcessSpecs {
    $profile = Get-WorkerProfile
    return @(
        (New-CeleryWorkerSpec -Name 'ai-worker' -Queue 'ai' -Pool $profile.AiPool -Concurrency $profile.AiConcurrency),
        (New-CeleryWorkerSpec -Name 'report-worker' -Queue 'report' -Pool 'solo' -Concurrency $profile.ReportConcurrency),
        (New-CeleryWorkerSpec -Name 'fetch-worker' -Queue 'fetch' -Pool 'threads' -Concurrency $profile.FetchConcurrency),
        (New-CeleryWorkerSpec -Name 'pipeline-worker' -Queue 'pipeline,web3-news-intel' -Pool 'threads' -Concurrency $profile.PipelineConcurrency),
        @{
            Name = 'scheduler'
            Args = @('-m', 'celery', '-A', 'app.workers.celery_app', 'beat', '--loglevel=INFO', "--schedule=$RuntimeDir\celerybeat-schedule")
        }
    )
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
            return @{ Ok = $false; Url = $url; Detail = 'connection timeout' }
        }
        $client.Dispose()
        return @{ Ok = $true; Url = $url; Detail = 'reachable' }
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
    $processId = 0
    if (-not ([int]::TryParse($raw, [ref]$processId))) {
        return $null
    }
    return Get-Process -Id $processId -ErrorAction SilentlyContinue
}

function Get-ManagedProcessIds {
    param([Parameter(Mandatory = $true)][array]$Specs)

    $ids = @()
    foreach ($spec in $Specs) {
        $process = Get-ManagedProcess $spec.Name
        if ($process) {
            $ids += [int]$process.Id
        }
    }
    return $ids
}

function Get-ProjectCeleryProcesses {
    if (-not (Test-Path -LiteralPath $Python)) {
        return @()
    }
    $pythonPath = [System.IO.Path]::GetFullPath($Python)
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue
    return @(
        $processes | Where-Object {
            $commandLine = $_.CommandLine
            $executablePath = $_.ExecutablePath
            $samePython = $false
            if ($executablePath) {
                try {
                    $samePython = ([System.IO.Path]::GetFullPath($executablePath) -ieq $pythonPath)
                } catch {
                    $samePython = $false
                }
            }
            $commandLine -and
                $samePython -and
                $commandLine.Contains('celery') -and
                $commandLine.Contains('app.workers.celery_app')
        }
    )
}

function Stop-ProjectCeleryProcesses {
    param([int[]]$ExceptProcessIds = @())

    foreach ($processInfo in @(Get-ProjectCeleryProcesses)) {
        $processId = [int]$processInfo.ProcessId
        if ($ExceptProcessIds -contains $processId) {
            continue
        }
        Write-Info "Stopping stale Celery process, PID=$processId"
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        foreach ($name in @('ai-worker', 'report-worker', 'fetch-worker', 'pipeline-worker', 'publisher-worker', 'scheduler')) {
            $pidFile = Get-PidFile $name
            if (Test-Path -LiteralPath $pidFile) {
                $raw = (Get-Content -LiteralPath $pidFile -TotalCount 1).Trim()
                if ($raw -eq [string]$processId) {
                    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }
}

function Start-ManagedProcess($Spec) {
    $name = $Spec.Name
    $existing = Get-ManagedProcess $name
    if ($existing) {
        Write-Info "$name already running, PID=$($existing.Id)"
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
    Write-Info "$name started, PID=$($process.Id), logs: $stdout / $stderr"
}

function Stop-ManagedProcess($Name) {
    $process = Get-ManagedProcess $Name
    if (-not $process) {
        Write-Info "$Name not running"
        Remove-Item -LiteralPath (Get-PidFile $Name) -Force -ErrorAction SilentlyContinue
        return
    }
    Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Get-PidFile $Name) -Force -ErrorAction SilentlyContinue
    Write-Info "$Name stopped, PID=$($process.Id)"
}

function Show-Status {
    $profile = Get-WorkerProfile
    Write-Info "Database: $($profile.DatabaseKind) ($(Get-DisplayDatabaseUrl))"
    Write-Info "Worker profile: $($profile.Name), ai concurrency=$($profile.AiConcurrency), report concurrency=$($profile.ReportConcurrency), fetch concurrency=$($profile.FetchConcurrency), pipeline concurrency=$($profile.PipelineConcurrency)"
    $redis = Test-Redis
    $redisStatus = if ($redis.Ok) { 'OK' } else { "DOWN: $($redis.Detail)" }
    Write-Info "Redis: $redisStatus ($($redis.Url))"
    $processes = Get-ProcessSpecs
    foreach ($spec in $processes) {
        $name = $spec.Name
        $process = Get-ManagedProcess $name
        $stdout = Get-LogFile $name 'out'
        $stderr = Get-LogFile $name 'err'
        if ($process) {
            Write-Info "${name}: running, PID=$($process.Id), logs: $stdout / $stderr"
        } else {
            Write-Info "${name}: not running, logs: $stdout / $stderr"
        }
    }
    $managedIds = Get-ManagedProcessIds -Specs $processes
    $staleProcesses = @(Get-ProjectCeleryProcesses | Where-Object { $managedIds -notcontains [int]$_.ProcessId })
    foreach ($processInfo in $staleProcesses) {
        Write-Info "Stale Celery process: PID=$($processInfo.ProcessId)"
    }
}

$selected = @($Status, $Start, $Stop).Where({ $_ }).Count
if ($selected -ne 1) {
    Write-Error 'Specify exactly one option: -Status, -Start, or -Stop'
}
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "Project Python venv not found: $Python"
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
Import-DotEnv

if ($Status) {
    Show-Status
    exit 0
}

$Processes = Get-ProcessSpecs

if ($Stop) {
    $toStop = @($Processes)
    [array]::Reverse($toStop)
    foreach ($spec in $toStop) {
        Stop-ManagedProcess $spec.Name
    }
    Stop-ProjectCeleryProcesses
    Show-Status
    exit 0
}

$redis = Test-Redis
if (-not $redis.Ok) {
    Write-Info "Redis unavailable: $($redis.Detail)"
    Write-Info "Current broker: $($redis.Url)"
    Write-Info 'Start Redis first and confirm REDIS_URL/CELERY_BROKER_URL is reachable.'
    exit 2
}

$profile = Get-WorkerProfile
Write-Info "Using worker profile: $($profile.Name) (database=$($profile.DatabaseKind), ai concurrency=$($profile.AiConcurrency), report concurrency=$($profile.ReportConcurrency), fetch concurrency=$($profile.FetchConcurrency), pipeline concurrency=$($profile.PipelineConcurrency))"
foreach ($spec in @($Processes)) {
    Stop-ManagedProcess $spec.Name
}
Stop-ProjectCeleryProcesses

foreach ($spec in $Processes) {
    Start-ManagedProcess $spec
}
Show-Status
