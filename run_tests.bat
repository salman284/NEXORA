@echo off
echo ============================================
echo  NEXORA MongoDB Migration - Test Runner
echo ============================================
cd /d c:\Users\salma\Desktop\Coding\NEXORA

echo.
echo [1/3] Installing dependencies...
python -m pip install "pymongo[srv]" motor python-dotenv --quiet

echo.
echo [2/3] Verifying pymongo...
python -c "import pymongo; print('pymongo', pymongo.version, 'OK')"
python -c "import motor; print('motor OK')"
python -c "from dotenv import load_dotenv; print('dotenv OK')"

echo.
echo [3/3] Running tests...
python -m pytest backend\tests worker_agent\tests -v 2>&1

echo.
echo Done! Press any key to exit.
pause
