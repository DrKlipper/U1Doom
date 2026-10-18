@echo off
rem Starts the U1 DOOM page on Windows; the first start sets up .venv with paramiko (SSH).
cd /d "%~dp0" || exit /b 1
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || exit /b 1
    .venv\Scripts\pip install --quiet paramiko || exit /b 1
)
.venv\Scripts\python launcher.py
