@echo off
cd /d "%~dp0"
echo Starting Chess AI Assistant...
echo Browser will open at http://127.0.0.1:5000/
echo Press Ctrl+C to stop.
echo.
"%~dp0venv\Scripts\python.exe" "%~dp0main.py"
pause
