@echo off
rem tinglongWu developer update (#84): pull dev, reinstall packages, start the GUI. Double-click this file.
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo .venv not found: run install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m tinglongwu_setup.update
if errorlevel 1 (
  echo.
  echo Update failed, see the message above.
  pause
  exit /b 1
)
