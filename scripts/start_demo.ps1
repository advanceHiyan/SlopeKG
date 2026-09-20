param(
    [int]$Port = 8766,
    [switch]$RunPipeline,
    [ValidateSet("basic", "deep")][string]$Mode = "basic",
    [int]$OcrPages = 0,
    [ValidateSet("auto", "cpu", "gpu")][string]$OcrDevice = "auto",
    [ValidateRange(1, 16)][int]$OcrWorkers = 4,
    [ValidateRange(0, 32)][int]$OcrBatchSize = 0
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
$env:PADDLEOCR_DISABLE_AUTO_LOGGING_CONFIG = "1"
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = "True"

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
    Write-Host "[1/2] Updating parsed data and graph..."
    if ($OcrPages -gt 0) {
        Write-Host "  OCR is enabled for up to $OcrPages queued pages."
        Write-Host "  OCR may show several model progress bars; this is one pipeline, not repeated server startup."
    } else {
        Write-Host "  OCR is disabled for this startup update. Use -OcrPages N when OCR is needed."
    }
    & $Python scripts\run_pipeline.py --mode $Mode --ocr-pages $OcrPages --ocr-device $OcrDevice --ocr-workers $OcrWorkers --ocr-batch-size $OcrBatchSize
    if ($LASTEXITCODE -ne 0) {
        throw "Pipeline failed with exit code $LASTEXITCODE"
    }
}

Write-Host $(if ($RunPipeline) { "[2/2] Starting SlopeKG demo service..." } else { "[1/1] Starting SlopeKG demo service..." })
Write-Host "  $Url"
Write-Host "  One server process will be started. The rawPDF watcher runs inside this process and only schedules work after files change."
Write-Host "Press Ctrl+C to stop."
& $Python scripts\serve_demo.py --port $Port
