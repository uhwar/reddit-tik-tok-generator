@echo off
chcp 65001 >nul
echo =========================================
echo   Reddit Story Selector CLI
echo =========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    echo Please install Python 3.x from https://python.org
    pause
    exit /b 1
)

REM Navigate to backend directory
cd /d "%~dp0\backend"

REM Check if virtual environment exists, create if not
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install/update requirements
echo Checking dependencies...
pip install -q -r requirements.txt

REM Run the CLI
echo.
echo Starting Reddit Story Selector...
echo =========================================
python cli.py

REM Deactivate virtual environment
call venv\Scripts\deactivate.bat

pause
