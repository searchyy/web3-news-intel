param(
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [switch]$OpenBrowser
)

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http

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
$FrontendDir = Join-Path $ProjectRoot 'frontend'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$DevRuntime = Join-Path $ProjectRoot 'scripts\dev_runtime.ps1'
$EnvFile = Join-Path $ProjectRoot '.env'
$RedisExe = Join-Path $RuntimeDir 'redis-5.0.14.1\redis-server.exe'
$RedisConf = Join-Path $RuntimeDir 'redis-dev.conf'
$ApiBaseUrl = 'http://127.0.0.1:59134'
$ApiHealthUrl = "$ApiBaseUrl/health"
$FrontendUrl = 'http://127.0.0.1:5173/'
$DefaultDatabaseUrl = 'postgresql+psycopg://web3_news:web3_news@127.0.0.1:15432/web3_news_intel'

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
    if (-not [int]::TryParse($raw, [ref]$processId)) {
        return $null
    }
    return Get-Process -Id $processId -ErrorAction SilentlyContinue
}

function Start-ManagedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    $existing = Get-ManagedProcess $Name
    if ($existing) {
        Write-Info "$Name already running, PID=$($existing.Id)"
        return $existing
    }
    $stdout = Get-LogFile $Name 'out'
    $stderr = Get-LogFile $Name 'err'
    $process = Start-Process -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -Encoding UTF8 -LiteralPath (Get-PidFile $Name) -Value ([string]$process.Id)
    Write-Info "$Name started, PID=$($process.Id), logs: $stdout / $stderr"
    return $process
}

function Stop-ManagedProcess($Name) {
    $process = Get-ManagedProcess $Name
    if (-not $process) {
        Write-Info "$Name not managed by one-click runtime or not running"
        Remove-Item -LiteralPath (Get-PidFile $Name) -Force -ErrorAction SilentlyContinue
        return
    }
    Stop-Process -Id $process.Id
    Remove-Item -LiteralPath (Get-PidFile $Name) -Force -ErrorAction SilentlyContinue
    Write-Info "$Name stopped, PID=$($process.Id)"
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

function Get-PostgresEndpoint {
    $databaseUrl = Get-DatabaseUrl
    $normalizedUrl = $databaseUrl -replace '^postgresql\+[^:]+://', 'postgresql://'
    $normalizedUrl = $normalizedUrl -replace '^postgres://', 'postgresql://'
    try {
        $uri = [Uri]$normalizedUrl
        if (-not $uri.Host) {
            return @{ Ok = $false; Detail = 'missing host' }
        }
        $port = if ($uri.Port -gt 0) { $uri.Port } else { 5432 }
        return @{ Ok = $true; Host = $uri.Host; Port = $port }
    } catch {
        return @{ Ok = $false; Detail = $_.Exception.Message }
    }
}
function Test-TcpEndpoint($HostName, [int]$Port, [int]$TimeoutMs = 1500) {
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $task = $client.ConnectAsync($HostName, $Port)
        if (-not $task.Wait($TimeoutMs)) {
            $client.Dispose()
            return @{ Ok = $false; Detail = 'timeout' }
        }
        $client.Dispose()
        return @{ Ok = $true; Detail = 'reachable' }
    } catch {
        return @{ Ok = $false; Detail = $_.Exception.Message }
    }
}

function Test-Database {
    $kind = Get-DatabaseKind
    if ($kind -eq 'sqlite') {
        return @{ Ok = $true; Kind = $kind; Detail = 'SQLite file mode; low-concurrency worker profile' }
    }
    if ($kind -eq 'postgresql') {
        $endpoint = Get-PostgresEndpoint
        if (-not $endpoint.Ok) {
            return @{ Ok = $false; Kind = $kind; Detail = $endpoint.Detail }
        }
        $result = Test-TcpEndpoint $endpoint.Host $endpoint.Port
        return @{ Ok = $result.Ok; Kind = $kind; Detail = "$($endpoint.Host):$($endpoint.Port) $($result.Detail)" }
    }
    return @{ Ok = $true; Kind = $kind; Detail = 'database type not checked by local runtime' }
}

function Start-ComposePostgresIfLocal {
    if ((Get-DatabaseKind) -ne 'postgresql') {
        return $false
    }
    $endpoint = Get-PostgresEndpoint
    if (-not $endpoint.Ok) {
        return $false
    }
    $hostName = $endpoint.Host.ToLowerInvariant()
    if ($hostName -notin @('localhost', '127.0.0.1', '::1')) {
        return $false
    }
    if ($endpoint.Port -ne 15432) {
        return $false
    }
    if ((Test-TcpEndpoint $endpoint.Host $endpoint.Port).Ok) {
        return $true
    }
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        return $false
    }
    Write-Info "PostgreSQL is not reachable on $($endpoint.Host):$($endpoint.Port); trying docker compose up -d postgres"
    Push-Location $ProjectRoot
    try {
        & $docker.Source compose up -d postgres
        if ($LASTEXITCODE -ne 0) {
            Write-Info "docker compose up -d postgres failed, exit=$LASTEXITCODE"
            return $false
        }
    } finally {
        Pop-Location
    }
    return (Wait-Until 'PostgreSQL' { Test-Database } 60)
}
function Test-Redis {
    $url = Get-BrokerUrl
    try {
        $uri = [Uri]$url
        $hostName = $uri.Host
        $port = if ($uri.Port -gt 0) { $uri.Port } else { 6379 }
        $result = Test-TcpEndpoint $hostName $port
        $result['Url'] = $url
        return $result
    } catch {
        return @{ Ok = $false; Url = $url; Detail = $_.Exception.Message }
    }
}

function Test-HttpEndpoint($Url, [int]$TimeoutSeconds = 4) {
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSeconds)
    try {
        $response = $client.GetAsync($Url).GetAwaiter().GetResult()
        $statusCode = [int]$response.StatusCode
        $detail = "$statusCode $($response.ReasonPhrase)"
        return @{ Ok = $response.IsSuccessStatusCode; Detail = $detail }
    } catch {
        return @{ Ok = $false; Detail = $_.Exception.Message }
    } finally {
        $client.Dispose()
        $handler.Dispose()
    }
}

function Get-ListeningPid([int]$Port) {
    try {
        $pattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
        foreach ($line in (& netstat.exe -ano -p tcp 2>$null)) {
            if ($line -match $pattern) {
                return [int]$Matches[1]
            }
        }
    } catch {
        return $null
    }
    return $null
}

function Adopt-ListeningProcess($Name, [int]$Port, $Label) {
    if (Get-ManagedProcess $Name) {
        return
    }
    $processId = Get-ListeningPid $Port
    if (-not $processId) {
        return
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $process) {
        return
    }
    Set-Content -Encoding UTF8 -LiteralPath (Get-PidFile $Name) -Value ([string]$process.Id)
    Write-Info "$Label adopted, PID=$($process.Id)"
}

function Wait-Until($Label, [scriptblock]$Probe, [int]$TimeoutSeconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $result = @{ Ok = $false; Detail = 'not started' }
    do {
        $result = & $Probe
        if ($result.Ok) {
            Write-Info "$Label OK ($($result.Detail))"
            return $true
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    Write-Info "$Label not ready ($($result.Detail))"
    return $false
}

function Start-Redis {
    $redis = Test-Redis
    if ($redis.Ok) {
        Write-Info "Redis already reachable ($($redis.Url))"
        return
    }
    if (-not (Test-Path -LiteralPath $RedisExe)) {
        throw "Redis executable not found: $RedisExe"
    }
    if (-not (Test-Path -LiteralPath $RedisConf)) {
        throw "Redis config not found: $RedisConf"
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeDir 'redis-data') | Out-Null
    Start-ManagedProcess -Name 'redis' -FilePath $RedisExe -ArgumentList @($RedisConf) -WorkingDirectory $ProjectRoot | Out-Null
    Wait-Until 'Redis' { Test-Redis } 20 | Out-Null
}

function Assert-DatabaseReady {
    $database = Test-Database
    if ($database.Kind -eq 'sqlite') {
        Write-Info "Database: SQLite detected ($(Get-DisplayDatabaseUrl)); using low worker concurrency. Use PostgreSQL for sustained local runs."
        return
    }
    if ($database.Kind -eq 'postgresql') {
        if (-not $database.Ok) {
            if (Start-ComposePostgresIfLocal) {
                $database = Test-Database
            }
        }
        if (-not $database.Ok) {
            throw "PostgreSQL is not reachable ($($database.Detail)). Current DATABASE_URL=$(Get-DisplayDatabaseUrl). Start PostgreSQL or update .env before running one-click startup."
        }
        Write-Info "Database: PostgreSQL OK ($($database.Detail))"
        return
    }
    Write-Info "Database: $($database.Kind) ($($database.Detail)); using conservative worker settings."
}

function Invoke-DatabaseMigrations {
    if ((Get-DatabaseKind) -ne 'postgresql') {
        Write-Info 'Skipping automatic migrations for non-PostgreSQL database.'
        return
    }
    $stdout = Get-LogFile 'alembic' 'out'
    $stderr = Get-LogFile 'alembic' 'err'
    Write-Info "Running PostgreSQL migrations: python -m alembic upgrade head"
    $process = Start-Process -FilePath $Python `
        -ArgumentList @('-m', 'alembic', 'upgrade', 'head') `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($process.ExitCode -ne 0) {
        throw "PostgreSQL migration failed, exit=$($process.ExitCode). Logs: $stdout / $stderr"
    }
    Write-Info "PostgreSQL migrations OK, logs: $stdout / $stderr"
}
function Resolve-NpmCommand {
    $command = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $command = Get-Command npm -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    throw 'npm not found. Install Node.js and make sure npm is in PATH.'
}

function Start-Api {
    $api = Test-HttpEndpoint $ApiHealthUrl
    if ($api.Ok) {
        Write-Info "API already online ($ApiHealthUrl)"
        Adopt-ListeningProcess 'api-dev' 59134 'API process'
        return
    }
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "Project Python venv not found: $Python"
    }
    Start-ManagedProcess -Name 'api-dev' -FilePath $Python -ArgumentList @('scripts\dev_api.py', '--host', '127.0.0.1', '--port', '59134') -WorkingDirectory $ProjectRoot | Out-Null
    Wait-Until 'API' { Test-HttpEndpoint $ApiHealthUrl } 45 | Out-Null
}

function Start-Frontend {
    $frontend = Test-HttpEndpoint $FrontendUrl
    if ($frontend.Ok) {
        Write-Info "Frontend already online ($FrontendUrl)"
        Adopt-ListeningProcess 'frontend' 5173 'Frontend process'
        return
    }
    $npm = Resolve-NpmCommand
    Start-ManagedProcess -Name 'frontend' -FilePath $npm -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1') -WorkingDirectory $FrontendDir | Out-Null
    Wait-Until 'Frontend' { Test-HttpEndpoint $FrontendUrl } 45 | Out-Null
}

function Start-Workers {
    & $DevRuntime -Stop
    & $DevRuntime -Start
}

function Stop-Workers {
    & $DevRuntime -Stop
}

function Write-ManagedStatus($Name, $Label) {
    $process = Get-ManagedProcess $Name
    if ($process) {
        Write-Info "${Label}: running, PID=$($process.Id), logs: $(Get-LogFile $Name 'out') / $(Get-LogFile $Name 'err')"
    } else {
        Write-Info "${Label}: not managed by one-click runtime or not running"
    }
}

function Show-FeishuSchedules {
    if (-not (Test-Path -LiteralPath $Python)) {
        Write-Info 'Feishu schedules: cannot check, Python venv not found'
        return
    }
    $scriptPath = Join-Path $RuntimeDir 'feishu_schedule_status.py'
    $codeLines = @(
        'import sys',
        'from zoneinfo import ZoneInfo',
        '',
        'sys.stdout.reconfigure(encoding="utf-8")',
        '',
        'from sqlalchemy import select',
        'from sqlalchemy.orm import selectinload',
        '',
        'from app.core.time import ensure_utc',
        'from app.db.models import ReportSchedule',
        'from app.db.session import SessionLocal',
        '',
        'with SessionLocal() as session:',
        '    schedules = list(',
        '        session.scalars(',
        '            select(ReportSchedule)',
        '            .options(selectinload(ReportSchedule.destination))',
        '            .order_by(ReportSchedule.id)',
        '        )',
        '    )',
        '    if not schedules:',
        '        print("[web3-news-intel] Feishu schedules: none")',
        '    for schedule in schedules:',
        '        if schedule.next_run_at:',
        '            zone = ZoneInfo(schedule.timezone or "UTC")',
        '            next_run = ensure_utc(schedule.next_run_at).astimezone(zone)',
        '            next_text = next_run.strftime("%Y-%m-%d %H:%M")',
        '        else:',
        '            next_text = "not set"',
        '        status = "enabled" if schedule.enabled else "disabled"',
        '        result = schedule.last_result or "-"',
        '        destination = schedule.destination.name if schedule.destination else "-"',
        '        print(f"[web3-news-intel] Feishu schedule #{schedule.id}: {status}, name={schedule.name}, next={next_text}, result={result}, destination={destination}")'
    )
    try {
        [System.IO.File]::WriteAllLines($scriptPath, $codeLines, [System.Text.UTF8Encoding]::new($false))
        & $Python $scriptPath
    } catch {
        Write-Info "Feishu schedules: check failed ($($_.Exception.Message))"
    } finally {
        Remove-Item -LiteralPath $scriptPath -Force -ErrorAction SilentlyContinue
    }
}

function Show-Status {
    $database = Test-Database
    if ($database.Ok) {
        Write-Info "Database: OK ($($database.Kind), $($database.Detail), $(Get-DisplayDatabaseUrl))"
    } else {
        Write-Info "Database: DOWN ($($database.Kind), $($database.Detail), $(Get-DisplayDatabaseUrl))"
    }
    $redis = Test-Redis
    if ($redis.Ok) {
        Write-Info "Redis: OK ($($redis.Url))"
    } else {
        Write-Info "Redis: DOWN ($($redis.Detail))"
    }
    $api = Test-HttpEndpoint $ApiHealthUrl
    if ($api.Ok) {
        Write-Info "API: OK ($ApiHealthUrl)"
    } else {
        Write-Info "API: DOWN ($($api.Detail))"
    }
    $frontend = Test-HttpEndpoint $FrontendUrl
    if ($frontend.Ok) {
        Write-Info "Frontend: OK ($FrontendUrl)"
    } else {
        Write-Info "Frontend: DOWN ($($frontend.Detail))"
    }
    Write-ManagedStatus 'redis' 'Redis process'
    Write-ManagedStatus 'api-dev' 'API process'
    Write-ManagedStatus 'frontend' 'Frontend process'
    & $DevRuntime -Status
    Show-FeishuSchedules
}

$selected = @($Start, $Stop, $Status).Where({ $_ }).Count
if ($selected -ne 1) {
    Write-Error 'Specify exactly one option: -Start, -Stop, or -Status'
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
Import-DotEnv

if ($Status) {
    Show-Status
    exit 0
}

if ($Stop) {
    Stop-ManagedProcess 'frontend'
    Stop-Workers
    Stop-ManagedProcess 'api-dev'
    Stop-ManagedProcess 'redis'
    Show-Status
    exit 0
}

Start-Redis
Assert-DatabaseReady
Invoke-DatabaseMigrations
Start-Api
Start-Workers
Start-Frontend
Show-Status

if ($OpenBrowser) {
    Start-Process $FrontendUrl | Out-Null
}
