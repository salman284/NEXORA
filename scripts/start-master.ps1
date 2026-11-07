[CmdletBinding()]
param(
    [string]$Host = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 5000
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
python -m pip install -r backend\requirements.txt
python -m uvicorn backend.app.main:app --reload --host $Host --port $Port