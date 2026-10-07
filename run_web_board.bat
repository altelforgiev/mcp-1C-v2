@echo off
rem ===================================================================
rem Startup script for 1C Web Board (app.py) with secrets.env auto-load
rem ===================================================================

rem Set UTF-8 encoding
chcp 65001 > nul

rem Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found in PATH. Please install Python 3.10+ and add it to system environment variables.
    pause
    exit /b 1
)

rem Check secrets.env
if exist "secrets.env" (
    echo [INFO] Loading AI connection keys from secrets.env...
) else (
    echo [NOTICE] secrets.env not found. Creating from template...
    copy secrets.env.template secrets.env > nul
)

echo ===================================================================
echo Starting 1C Query AI Web Board...
echo URL: http://localhost:8000
echo ===================================================================
echo.

rem Launch default browser
start http://localhost:8000

rem Start python app.py
python app.py 8000 "data\metadata.json"

if %errorlevel% neq 0 (
    echo:
    echo [ERROR] Web board server exited with error (code: %errorlevel%).
    pause
)
