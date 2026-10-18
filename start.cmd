@echo off
rem Starts the U1 DOOM page on Windows; the first start sets up .venv-windows with paramiko (SSH).
rem A folder of its own: WSL's start.sh may use .venv in the same checkout.
cd /d "%~dp0" || exit /b 1
rem Checks for paramiko itself: a first start that broke off leaves the folder without it.
.venv-windows\Scripts\python -c "import paramiko" 2>nul || (
    python -m venv .venv-windows || exit /b 1
    .venv-windows\Scripts\pip install --quiet paramiko || exit /b 1
)
.venv-windows\Scripts\python launcher.py
