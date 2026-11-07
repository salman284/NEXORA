[CmdletBinding()]
param(
    [string]$MasterUrl = "http://127.0.0.1:5000",
    [string]$NodeId,
    [string]$PairingCode,
    [double]$Interval = 2,
    [double]$ExecutionTimeout = 30
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
python -m pip install -r worker_agent\requirements.txt
if (-not $env:NEXORA_ACCESS_TOKEN) {
    throw "Set NEXORA_ACCESS_TOKEN before starting the Worker Agent."
}
if ($PairingCode) {
    python -m worker_agent.agent.main --master-url $MasterUrl --pairing-code $PairingCode
} elseif ($NodeId) {
    python -m worker_agent.agent.main --master-url $MasterUrl --node-id $NodeId --interval $Interval --execution-timeout $ExecutionTimeout
} else {
    throw "Provide -PairingCode for onboarding or -NodeId for an approved Worker."
}