param([switch]$Check)
$ErrorActionPreference = 'Stop'

# CUDA 13 puts runtime DLLs in bin/x64; earlier toolkits use bin.
# Change only this process and, in CI, subsequent job steps.
$CudaRuntimeRoot = $env:CUDA_PATH
if (-not $CudaRuntimeRoot -or -not (Test-Path $CudaRuntimeRoot)) {
    if ($Check) { throw 'CUDA_PATH must point to an installed CUDA Toolkit.' }
    return
}
$CudaRuntimePaths = @('bin/x64', 'bin') | ForEach-Object {
    Join-Path $CudaRuntimeRoot $_
} | Where-Object { Test-Path $_ }
foreach ($CudaRuntimePath in $CudaRuntimePaths) {
    $env:PATH = "$CudaRuntimePath;$env:PATH"
    if ($env:GITHUB_PATH) {
        $CudaRuntimePath | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append
    }
}
if (-not $Check) { return }

# Verify the same runtime and import libraries before the long
# CUDA kernel build. No device or model is needed for this loader check.
$CudaProbeDir = Join-Path ([IO.Path]::GetTempPath()) ('backburner-cuda-probe-' + [guid]::NewGuid())
New-Item -ItemType Directory $CudaProbeDir | Out-Null
@'
cmake_minimum_required(VERSION 3.18)
project(backburner_cuda_runtime_check LANGUAGES CXX)
find_package(CUDAToolkit REQUIRED)
add_executable(cuda-runtime-check main.cpp)
set_property(TARGET cuda-runtime-check PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
target_link_libraries(cuda-runtime-check PRIVATE CUDA::cudart_static CUDA::cublas CUDA::cuda_driver)
'@ | Set-Content -Path "$CudaProbeDir/CMakeLists.txt" -Encoding utf8
@'
#include <cstdio>
#include <cuda_runtime_api.h>
#include <cublas_v2.h>
#include <cuda.h>
int main(int argc, char **) {
    std::puts("CUDA loader reached main");
    std::fflush(stdout);
    // Retain the driver and cuBLAS imports without invoking GPU operations.
    if (argc > 1) {
        int version = 0;
        return int(cuDriverGetVersion(&version)) + int(cublasGetVersion(nullptr, &version));
    }
    std::puts(cudaGetErrorString(cudaSuccess));
    return 0;
}
'@ | Set-Content -Path "$CudaProbeDir/main.cpp" -Encoding utf8
& cmake -S $CudaProbeDir -B "$CudaProbeDir/build"
if ($LASTEXITCODE -ne 0) { throw 'CUDA runtime check configuration failed' }
& cmake --build "$CudaProbeDir/build" --config Release
if ($LASTEXITCODE -ne 0) { throw 'CUDA runtime check build failed' }
$CudaProbeExecutable = "$CudaProbeDir/build/Release/cuda-runtime-check.exe"
$CudaVsWhere = "${env:ProgramFiles(x86)}/Microsoft Visual Studio/Installer/vswhere.exe"
if (Test-Path $CudaVsWhere) {
    $CudaDumpbin = & $CudaVsWhere -latest -products '*' -find 'VC/Tools/MSVC/**/bin/Hostx64/x64/dumpbin.exe' | Select-Object -First 1
    if ($CudaDumpbin) { & $CudaDumpbin /dependents $CudaProbeExecutable }
}
Get-ChildItem (Join-Path $CudaRuntimeRoot 'bin') -Recurse -Filter '*.dll' |
    Where-Object { $_.Name -match '^(cudart64_|cublas64_|cublasLt64_)' } |
    ForEach-Object { Write-Host "CUDA runtime: $($_.FullName)" }
& $CudaProbeExecutable
if ($LASTEXITCODE -ne 0) {
    throw ('CUDA runtime DLL loading failed (exit 0x{0:X8})' -f [int]$LASTEXITCODE)
}
Write-Host 'CUDA runtime DLL loading passed (no GPU operations).'
