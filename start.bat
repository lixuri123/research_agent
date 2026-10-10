@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Python environment missing. Run uv sync in the project directory first.
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\start.py" %*
exit /b %errorlevel%
