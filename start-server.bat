@echo off
setlocal ENABLEDELAYEDEXPANSION
REM Resolve script directory
set "SCRIPT_DIR=%~dp0"
REM Games dir is a sibling of the current folder: ..\Games
for %%I in ("%SCRIPT_DIR%..") do set "PARENT=%%~fI"
set "GAMES_ROOT=%PARENT%\Games"

if not exist "%GAMES_ROOT%" mkdir "%GAMES_ROOT%"

REM Activate venv if present (optional)
if exist "%SCRIPT_DIR%.venv\Scripts\activate.bat" call "%SCRIPT_DIR%.venv\Scripts\activate.bat"

python -m pip install -r "%SCRIPT_DIR%requirements.txt"

set "FLASK_SECRET=prod-%RANDOM%%RANDOM%"
set "GAMES_ROOT=%GAMES_ROOT%"
set "BIND=127.0.0.1"
set "PORT=5000"

python "%SCRIPT_DIR%app.py" "%GAMES_ROOT%"
