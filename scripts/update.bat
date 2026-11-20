@echo off
rem tinglongWu developer update (#84): pull dev, reinstall packages, start the GUI. Double-click this file.
chcp 65001 >nul
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo .venv not found: run install.bat first.
  pause
  exit /b 1
)
rem #1664: pre-move root package folders shadow src\ packages; remove them before any python -m (only once src\ exists)
if exist "src\core\tinglongwu\__init__.py" (
  for %%d in (tinglongwu tinglongwu_setup tlwu_app tlwu_eval) do if exist "%%d\" rmdir /s /q "%%d"
)
".venv\Scripts\python.exe" -m tinglongwu_setup.update
if errorlevel 1 (
  echo.
  echo Update failed, see the message above.
  pause
  exit /b 1
)
