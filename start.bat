@echo off
echo [1/3] Installing dependencies...
pip install -r requirements.txt

echo.
echo [2/3] Starting Backend API Server...
start cmd /k "cd backend && python -m uvicorn main:app --port 8000 --reload"

echo.
echo [3/3] Starting Frontend Server...
start cmd /k "cd frontnd && python -m http.server 9292"

echo.
echo Everything is ready! Opening browser...
timeout /t 3 /nobreak >nul
start http://localhost:9292/index.html
