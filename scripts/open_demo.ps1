param(
    [ValidateRange(1, 65535)][int]$Port = 8766
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = "D:\environment\Anaconda\envs\slopekg\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { $Python = "python" }
$ProbeScript = Join-Path $PSScriptRoot "check_demo_service.py"
$Url = "http://127.0.0.1:$Port/web/index.html"
$StatusUrl = "http://127.0.0.1:$Port/api/status"
$LogDirectory = Join-Path $Root "output\demo"
$StdoutLog = Join-Path $LogDirectory "launcher.stdout.log"
$StderrLog = Join-Path $LogDirectory "launcher.stderr.log"
$Mutex = [Threading.Mutex]::new($false, "Local\SlopeKG.Demo.$Port")
$LockTaken = $false

function Test-DemoReady {
    try {
        $Status = (& $Python $ProbeScript --port $Port | ConvertFrom-Json)
        return ($LASTEXITCODE -eq 0 -and $Status.state -eq "ready")
    } catch {
        return $false
    }
}

try {
    # Serialize rapid double-clicks so only one service is launched.
    try {
        $LockTaken = $Mutex.WaitOne(60000)
    } catch [Threading.AbandonedMutexException] {
        $LockTaken = $true
    }
    if (-not $LockTaken) {
        throw "Another SlopeKG launch is still in progress. Please try again shortly."
    }

    if (-not (Test-DemoReady)) {
        $null = New-Item -ItemType Directory -Path $LogDirectory -Force
        $PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        $StartupScript = Join-Path $PSScriptRoot "start_demo.ps1"
        $Service = Start-Process -FilePath $PowerShellExe -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$StartupScript`"", "-Port", $Port
        ) -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $StdoutLog `
            -RedirectStandardError $StderrLog -PassThru

        $Deadline = [DateTime]::UtcNow.AddSeconds(60)
        while (-not (Test-DemoReady)) {
            $Service.Refresh()
            if ($Service.HasExited) {
                throw "The SlopeKG startup process exited (code $($Service.ExitCode))."
            }
            if ([DateTime]::UtcNow -ge $Deadline) {
                throw "SlopeKG did not become ready within 60 seconds."
            }
            Start-Sleep -Milliseconds 500
        }
    }

    # The user requested a visible browser; the service stays in the background.
    Start-Process -FilePath $Url
} catch {
    $Message = "$($_.Exception.Message)`r`n`r`nStartup logs:`r`n$StderrLog`r`n$StdoutLog"
    Write-Error -Message $Message -ErrorAction Continue
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        $Message, "SlopeKG startup failed", "OK", "Error"
    ) | Out-Null
    exit 1
} finally {
    if ($LockTaken) { $Mutex.ReleaseMutex() }
    $Mutex.Dispose()
}
