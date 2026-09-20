param(
    [ValidateSet("auto", "gpu", "cpu")][string]$Backend = "auto",
    [string]$Python = "D:\environment\Anaconda\envs\slopekg\python.exe"
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()

if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

if ($Backend -eq "auto") {
    $NvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    $Backend = if ($NvidiaSmi) { "gpu" } else { "cpu" }
}

if ($Backend -eq "gpu") {
    Write-Host "Installing the GPU-first OCR backend (CUDA 12.9 runtime)..."
    & $Python -m pip uninstall -y paddlepaddle
    & $Python -m pip install paddlepaddle-gpu==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu129/
} else {
    Write-Host "Installing the portable CPU OCR backend..."
    & $Python -m pip uninstall -y paddlepaddle-gpu
    & $Python -m pip install paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
}

if ($LASTEXITCODE -ne 0) {
    throw "OCR backend installation failed with exit code $LASTEXITCODE"
}

& $Python -c "import paddle; print('Paddle', paddle.__version__); print('CUDA', paddle.device.is_compiled_with_cuda()); paddle.utils.run_check()"
if ($LASTEXITCODE -ne 0) {
    throw "PaddlePaddle verification failed with exit code $LASTEXITCODE"
}
