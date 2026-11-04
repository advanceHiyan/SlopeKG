param(
    [int]$Port = 8766,
    [switch]$RunPipeline,
    [ValidateSet("basic")][string]$Mode = "basic",
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

$ProbeScript = Join-Path $PSScriptRoot "check_demo_service.py"
$ServiceState = (& $Python $ProbeScript --port $Port | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $null -eq $ServiceState.state) {
    throw "Cannot verify the SlopeKG service on port $Port. No process was stopped."
}
if ($ServiceState.state -eq "ready") {
    Write-Host "SlopeKG is running with the current code and debug APIs: $Url"
    exit 0
}
if ($ServiceState.state -in @("busy", "unknown", "degraded")) {
    throw "$($ServiceState.reason) (port $Port). No process was stopped."
}
if ($ServiceState.state -eq "stale") {
    $ServicePid = [int]$ServiceState.pid
    $Listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
        Where-Object { $_.OwningProcess -eq $ServicePid } | Select-Object -First 1
    $Owner = Get-CimInstance Win32_Process -Filter "ProcessId = $ServicePid" -ErrorAction Stop
    $ExpectedPython = (Get-Command $Python -ErrorAction Stop).Source
    if ($null -eq $Listener -or $Owner.ExecutablePath -ne $ExpectedPython -or
        $Owner.CommandLine -notmatch 'serve_demo\.py' -or
        $Owner.CommandLine -notmatch "--port\s+$Port(?:\s|$)") {
        throw "The old process identity could not be confirmed. No process was stopped."
    }
    # The probe checked ordinary/rule jobs, isolated tests and the shared lock.
    # Recheck immediately before stopping the identified idle server.
    $Recheck = (& $Python $ProbeScript --port $Port | ConvertFrom-Json)
    if ($Recheck.state -ne "stale" -or $Recheck.pid -ne $ServicePid) {
        throw "Service state changed during validation. No process was stopped."
    }
    Write-Host "Reloading the idle SlopeKG service on port $Port to load current code..."
    Stop-Process -Id $ServicePid -ErrorAction Stop
    Wait-Process -Id $ServicePid -Timeout 5 -ErrorAction SilentlyContinue
} elseif (Test-PortListening -LocalPort $Port) {
    throw "Port $Port is occupied by an unrecognized service. No process was stopped."
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
