[CmdletBinding()]
param(
    [string]$MasterUrl,
    [string]$NodeId,
    [string]$PairingCode,
    [string]$NodeName = $env:COMPUTERNAME,
    [double]$Interval = 2,
    [double]$ExecutionTimeout = 30
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# --- Resolve Master URL ---
# Priority: explicit param > env var > default localhost
if (-not $MasterUrl) {
    if ($env:NEXORA_MASTER_URL) {
        $MasterUrl = $env:NEXORA_MASTER_URL
    } else {
        $MasterUrl = "http://127.0.0.1:5000"
    }
}

# --- Resolve Token ---
$token = $env:NEXORA_ACCESS_TOKEN
if (-not $token) {
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Yellow
    Write-Host "  NEXORA Worker Agent — Login Required" -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Yellow
    Write-Host "Master URL: $MasterUrl" -ForegroundColor Gray
    Write-Host ""
    $username = Read-Host "Username"
    $secPass  = Read-Host "Password" -AsSecureString
    $plainPass = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secPass))

    $body = @{ username = $username; password = $plainPass } | ConvertTo-Json
    try {
        $resp  = Invoke-RestMethod -Uri "$MasterUrl/api/v1/auth/login" `
                     -Method Post -ContentType "application/json" -Body $body
        $token = $resp.access_token
        Write-Host "Login successful! Token acquired." -ForegroundColor Green
    } catch {
        Write-Host "Login FAILED: $_" -ForegroundColor Red
        exit 1
    }
}

# --- Install dependencies ---
Write-Host ""
Write-Host "Installing worker dependencies..." -ForegroundColor Cyan
python -m pip install -r worker_agent\requirements.txt --quiet

# --- Launch Agent ---
Write-Host ""
if ($NodeId) {
    Write-Host "Starting Worker Agent (approved node)..." -ForegroundColor Cyan
    Write-Host "  Master  : $MasterUrl" -ForegroundColor Gray
    Write-Host "  Node ID : $NodeId" -ForegroundColor Gray
    Write-Host ""
    python -m worker_agent.agent.main `
        --master-url $MasterUrl `
        --node-id $NodeId `
        --token $token `
        --interval $Interval `
        --execution-timeout $ExecutionTimeout
} else {
    Write-Host "Connecting as Worker Node to Cluster..." -ForegroundColor Cyan
    Write-Host "  Master : $MasterUrl" -ForegroundColor Gray
    if ($PairingCode) {
        Write-Host "  Code   : $PairingCode" -ForegroundColor Gray
    } else {
        Write-Host "  Mode   : Open Join Request (Pending Master approval)" -ForegroundColor Yellow
    }
    Write-Host "  Name   : $NodeName" -ForegroundColor Gray
    Write-Host ""
    $pairingArgs = @(
        "-m", "worker_agent.agent.main",
        "--master-url", $MasterUrl,
        "--node-name", $NodeName,
        "--token", $token,
        "--interval", "$Interval",
        "--execution-timeout", "$ExecutionTimeout"
    )
    if ($PairingCode) {
        $pairingArgs += @("--pairing-code", $PairingCode)
    }
    python @pairingArgs
}