param(
    [Parameter(Mandatory=$true)][string]$Model,
    [string]$Output = "$HOME/Models/tail-iq4xs-L40-nohead.gguf",
    [int]$Layer = 40
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path $Model)) { throw "Model not found: $Model" }
$Output = [System.IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Force (Split-Path -Parent $Output) | Out-Null
& py scripts/split-gguf.py $Model $Output -L $Layer --no-head
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Tail written: $Output"
Write-Host "Next: py windows/push_tail.py `"$Output`""
