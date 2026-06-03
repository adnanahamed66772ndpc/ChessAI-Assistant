@echo off
cd /d "%~dp0"
echo Launching Chess AI Assistant (desktop)...
"%~dp0venv\Scripts\pythonw.exe" "%~dp0desktop_app.py"
