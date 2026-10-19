@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 exit /b 1
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8741/"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8741
