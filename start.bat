@echo off
cd /d "%~dp0"
set OPENBLAS_NUM_THREADS=1
set OMP_NUM_THREADS=1
set MPLCONFIGDIR=%~dp0artifacts\matplotlib
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.ps1 first. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 pause
