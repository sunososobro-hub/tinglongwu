@echo off
rem tinglongWu one-click installer. Double-click this file.
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "TLWU_RC=%ERRORLEVEL%"
echo.
pause
exit /b %TLWU_RC%
