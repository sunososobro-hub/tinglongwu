@echo off
rem #1712: update.bat lives in scripts\update.bat; older installs and shortcuts still double-click this root copy.
call "%~dp0scripts\update.bat" %*
exit /b %ERRORLEVEL%
