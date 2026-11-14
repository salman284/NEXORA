@echo off
echo ============================================
echo  NEXORA Backend - Start Server (MongoDB)
echo ============================================
cd /d c:\Users\salma\Desktop\Coding\NEXORA

echo Testing MongoDB connection...
python -c "
from dotenv import load_dotenv
load_dotenv()
import os
from pymongo import MongoClient
uri = os.getenv('MONGODB_URI', '')
if not uri:
    print('ERROR: MONGODB_URI not set in .env')
else:
    try:
        c = MongoClient(uri, serverSelectionTimeoutMS=5000)
        info = c.server_info()
        print('MongoDB connected! Version:', info['version'])
    except Exception as e:
        print('MongoDB connection FAILED:', e)
"

echo.
echo Starting backend server on port 5000...
echo (Dashboard will be at http://localhost:5000)
echo.
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 5000 --reload
