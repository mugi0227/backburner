param(
    [string]$BuildDir = 'llama.cpp/build-windows',
    [ValidateSet('cuda','vulkan','cpu')][string]$Backend = 'cpu'
)
$ErrorActionPreference = 'Stop'
$Bin = Join-Path $BuildDir 'bin/Release'
if (-not (Test-Path "$Bin/llama-server.exe")) { $Bin = Join-Path $BuildDir 'bin' }
if (-not (Test-Path "$Bin/llama-server.exe")) { throw "No Windows host in $BuildDir" }
$Stage = Join-Path 'dist' "backburner-windows-$Backend"
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force "$Stage/llama.cpp/build-windows/bin", "$Stage/scripts", "$Stage/docs" | Out-Null
Copy-Item "$Bin/llama-server.exe", "$Bin/llama-quantize.exe" "$Stage/llama.cpp/build-windows/bin/"
Get-ChildItem "$Bin/*.dll" -ErrorAction SilentlyContinue | Copy-Item -Destination "$Stage/llama.cpp/build-windows/bin/"
Copy-Item -Recurse windows $Stage
Remove-Item "$Stage/windows/test_patch.py"
Copy-Item LICENSE, README-WINDOWS.md $Stage
Copy-Item llama.cpp/LICENSE "$Stage/llama.cpp/"
Copy-Item docs/WINDOWS.md "$Stage/docs/"
Copy-Item scripts/split-gguf.py "$Stage/scripts/"
Copy-Item -Recurse llama.cpp/gguf-py "$Stage/llama.cpp/"
Get-ChildItem $Stage -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
New-Item -ItemType Directory -Force "$Stage/tests/security" | Out-Null
Copy-Item tests/security/noise-nnpsk0-vectors.json "$Stage/tests/security/"
Compress-Archive -Path "$Stage/*" -DestinationPath "dist/backburner-windows-$Backend.zip" -Force
Write-Host "Package: dist/backburner-windows-$Backend.zip"
