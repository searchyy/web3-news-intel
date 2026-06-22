param(
    [switch]$Quick,
    [switch]$Backend,
    [switch]$Frontend,
    [switch]$Full
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "未找到项目虚拟环境 Python：$Python"
}

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Command
    )
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Command
}

function Get-ChangedPythonTargets {
    $base = "origin/feat/multi-source-ai-search-performance"
    $changed = @()
    try {
        $changed = git diff --name-only --diff-filter=ACMRT "$base...HEAD"
    } catch {
        $changed = git diff --name-only --diff-filter=ACMRT HEAD
    }
    $targets = $changed | Where-Object {
        $_ -match "^(app|scripts|tests)/" -and $_ -match "\.py$"
    }
    if (-not $targets -or $targets.Count -eq 0) {
        return @("app", "scripts", "tests")
    }
    return $targets
}

function Invoke-BackendQuick {
    $targets = @(Get-ChangedPythonTargets)
    Invoke-Step "Ruff changed Python files" {
        & $Python -m ruff check @targets
    }
    Invoke-Step "Mypy relevant modules" {
        & $Python -m mypy app scripts
    }
    Invoke-Step "Affected backend tests" {
        & $Python -m pytest `
            tests/unit/test_admin_auth.py `
            tests/unit/test_ai_deepseek_backend.py `
            tests/integration/test_ai_admin_api.py `
            tests/unit/test_scheduler_polling.py `
            tests/unit/test_dev_poll_sources.py `
            -q
    }
}

function Invoke-FrontendQuick {
    Push-Location frontend
    try {
        Invoke-Step "Frontend typecheck" {
            npm run typecheck
        }
        Invoke-Step "Affected frontend tests" {
            npm run test -- tests/ai-settings.test.tsx tests/events-search.test.tsx tests/frontend-performance.test.ts
        }
    } finally {
        Pop-Location
    }
}

if (-not ($Quick -or $Backend -or $Frontend -or $Full)) {
    $Quick = $true
}

if ($Quick) {
    Invoke-BackendQuick
    Invoke-FrontendQuick
    exit 0
}

if ($Backend) {
    Invoke-Step "Ruff backend" {
        & $Python -m ruff check app scripts tests
    }
    Invoke-Step "Mypy backend" {
        & $Python -m mypy app scripts
    }
    Invoke-Step "Backend unit tests" {
        & $Python -m pytest tests/unit -q
    }
    Invoke-Step "Backend integration tests" {
        & $Python -m pytest tests/integration -q -m "not postgres and not redis and not celery and not compose and not live"
    }
}

if ($Frontend) {
    Push-Location frontend
    try {
        Invoke-Step "Frontend lint" {
            npm run lint
        }
        Invoke-Step "Frontend typecheck" {
            npm run typecheck
        }
        Invoke-Step "Frontend tests" {
            npm run test
        }
        Invoke-Step "Frontend build" {
            npm run build
        }
    } finally {
        Pop-Location
    }
}

if ($Full) {
    Invoke-Step "Pre-push acceptance" {
        & $Python scripts\pre_push_acceptance.py
    }
    Invoke-Step "Ruff all" {
        & $Python -m ruff check .
    }
    Invoke-Step "Mypy all" {
        & $Python -m mypy app scripts
    }
    Invoke-Step "Backend unit tests" {
        & $Python -m pytest tests/unit -q
    }
    Invoke-Step "Backend integration tests" {
        & $Python -m pytest tests/integration -q -m "not postgres and not redis and not celery and not compose and not live"
    }
    Invoke-Step "Source validation" {
        & $Python scripts\validate_sources.py sources.yaml
    }
    Push-Location frontend
    try {
        Invoke-Step "Frontend lint" {
            npm run lint
        }
        Invoke-Step "Frontend typecheck" {
            npm run typecheck
        }
        Invoke-Step "Frontend tests" {
            npm run test
        }
        Invoke-Step "Frontend build" {
            npm run build
        }
    } finally {
        Pop-Location
    }
}
