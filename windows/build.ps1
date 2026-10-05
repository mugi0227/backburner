param(
    [ValidateSet('cuda','vulkan','cpu')]
    [string]$Backend = 'cuda',
    [string]$BuildDir = 'llama.cpp/build-windows',
    [string]$CudaArchitectures = '',
    [ValidateRange(1,64)][int]$Parallel = 2
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path 'llama.cpp/CMakeLists.txt')) {
    throw 'Run this from the Backburner repository root (llama.cpp submodule must be present).'
}

& py windows/apply_windows_port.py llama.cpp
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$cmake = @('-S','llama.cpp','-B',$BuildDir,'-DCMAKE_BUILD_TYPE=Release','-DBUILD_SHARED_LIBS=OFF',
    '-DGGML_CUDA=OFF','-DGGML_VULKAN=OFF','-DLLAMA_OPENSSL=OFF',
    '-DLLAMA_BUILD_TESTS=OFF','-DLLAMA_BUILD_EXAMPLES=OFF','-DLLAMA_BUILD_APP=OFF')
switch ($Backend) {
    'cuda'   { $cmake += '-DGGML_CUDA=ON' }
    'vulkan' { $cmake += '-DGGML_VULKAN=ON' }
    'cpu'    { $cmake += '-DGGML_NATIVE=OFF' }
}
if ($Backend -eq 'cuda' -and $CudaArchitectures) {
    $cmake += "-DCMAKE_CUDA_ARCHITECTURES=$CudaArchitectures"
}

Write-Host "Configuring Backburner llama.cpp for Windows ($Backend)..."
& cmake @cmake
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& cmake --build $BuildDir --config Release --target llama-server llama-quantize --parallel $Parallel
exit $LASTEXITCODE
