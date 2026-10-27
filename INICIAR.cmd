@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" otimizador.py
) else (
  python otimizador.py
)
if errorlevel 1 pause
