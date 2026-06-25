param([switch]$NoBrowser)

$runtime = Join-Path $PSScriptRoot 'local_runtime.ps1'
if ($NoBrowser) {
    & $runtime -Start
} else {
    & $runtime -Start -OpenBrowser
}