[CmdletBinding()]
param(
    [string]$Host = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 5173
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "frontend")
npm install
npm run dev -- --host=$Host --port=$Port