param(
    [int]$Port = 8766,
    [switch]$RunPipeline,
    [ValidateSet("basic", "deep")][string]$Mode = "basic",
    [int]$OcrPages = 1000
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$CondaPython = "D:\environment\Anaconda\envs\slopekg\python.exe"
if (Test-Path $CondaPython) {
    $Python = $CondaPython
} else {
    $Python = "python"
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Url = "http://127.0.0.1:$Port/web/index.html"
$StatusUrl = "http://127.0.0.1:$Port/api/status"

function Test-PortListening {
    param([int]$LocalPort)
    try {
        $connections = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction Stop
        return @($connections).Count -gt 0
    } catch {
        $lines = netstat -ano | Select-String ":$LocalPort\s+.*LISTENING"
        return @($lines).Count -gt 0
    }
}

if (Test-PortListening -LocalPort $Port) {
    try {
        $null = Invoke-WebRequest -UseBasicParsing -Uri $StatusUrl -TimeoutSec 2
        Write-Host "SlopeKG demo already running:"
        Write-Host "  $Url"
        exit 0
    } catch {
        Write-Host "Port $Port is already in use, but it is not responding as SlopeKG demo."
        Write-Host "Use another port, for example:"
        Write-Host "  .\scripts\start_demo.ps1 -Port 8767"
        exit 1
    }
}

if ($RunPipeline) {
    Write-Host "Running pipeline..."
    & $Python scripts\run_pipeline.py --mode $Mode --ocr-pages $OcrPages
}

Write-Host "Starting SlopeKG demo..."
Write-Host "  $Url"
Write-Host "Press Ctrl+C to stop."
& $Python scripts\serve_demo.py --port $Port
