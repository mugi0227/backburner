param(
    [Parameter(Mandatory=$true)][string]$Model,
    [ValidateRange(1,65536)][int]$Context = 65536,
    [ValidateRange(1,65535)][int]$Port = 8080,
    [string]$BuildDir = 'llama.cpp/build-windows'
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/cuda_runtime.ps1"

if (-not (Test-Path $Model)) { throw "Model not found: $Model" }

$candidates = @(
    "$BuildDir/bin/Release/llama-server.exe",
    "$BuildDir/bin/llama-server.exe",
    "$BuildDir/Release/llama-server.exe"
)
$Server = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Server) { throw "llama-server.exe not found under $BuildDir. Run windows/build.ps1 first." }

# wifi_tunnel.py maps the iPhone's authenticated tail service to localhost:51060.
$env:LLAMA_SPLIT_TAIL = '127.0.0.1:51060'
$env:LLAMA_SPLIT_MIN = '512'
$env:LLAMA_SPLIT_ONE_BATCH = '1'
$env:LLAMA_SPLIT_L = '40'
# Do not inherit a Mac's phone-KV configuration; that path is outside this MVP.
Remove-Item Env:LLAMA_KV_REMOTE -ErrorAction SilentlyContinue
Remove-Item Env:LLAMA_KV_REMOTE_CTX -ErrorAction SilentlyContinue
Remove-Item Env:LLAMA_SPLIT_DECODE -ErrorAction SilentlyContinue

Write-Host 'Backburner Windows MVP: split prefill -> iPhone at localhost:51060'
Write-Host "OpenAI-compatible endpoint: http://127.0.0.1:$Port/v1"
Write-Host 'Keep Backburner open on the iPhone and windows/start_tunnel.py running.'

# Deliberately conservative MVP: no Mac-only SME/Metal/DFlash2 and no phone-held KV yet.
& $Server `
    -m $Model `
    -ngl 999 `
    -fa on `
    -c $Context `
    -np 1 `
    -ctk q8_0 `
    -ctv q8_0 `
    -ub 256 `
    --spec-type none `
    --jinja `
    --host 127.0.0.1 `
    --port $Port
exit $LASTEXITCODE
