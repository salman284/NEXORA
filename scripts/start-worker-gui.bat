@echo off
title NEXORA Worker Agent GUI
cd /d "%~dp0\.."
python -c "import tkinter" 2>nul
if %errorlevel% neq 0 (
    echo Error: Python or Tkinter is not found!
    pause
    exit /b 1
)
python -m pip install psutil requests --quiet 2>nul
start python nexora_worker_gui\worker_app.py
exit
