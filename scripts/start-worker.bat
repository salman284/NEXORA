@echo off
title NEXORA Worker Agent Launcher
echo ====================================================
echo        NEXORA Distributed GPU Worker Launcher
echo ====================================================
echo.

cd /d "%~dp0\.."

set /p MASTER_IP="Enter Master IP [Default: 10.229.205.230]: "
if "%MASTER_IP%"=="" set MASTER_IP=10.229.205.230

set /p PAIRING_CODE="Enter Pairing Code (Optional - Press Enter to skip): "

set /p NODE_NAME="Enter your Worker Name [Default: %COMPUTERNAME%]: "
if "%NODE_NAME%"=="" set NODE_NAME=%COMPUTERNAME%

echo.
echo Connecting to Master at http://%MASTER_IP%:5000 as '%NODE_NAME%'...
echo.

if "%PAIRING_CODE%"=="" (
    powershell -ExecutionPolicy Bypass -File "%~dp0start-worker.ps1" -MasterUrl "http://%MASTER_IP%:5000" -NodeName "%NODE_NAME%"
) else (
    powershell -ExecutionPolicy Bypass -File "%~dp0start-worker.ps1" -MasterUrl "http://%MASTER_IP%:5000" -PairingCode "%PAIRING_CODE%" -NodeName "%NODE_NAME%"
)

echo.
pause
