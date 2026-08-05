@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\toolkit.ps1" monthly-run %*
set "toolkit_exit=%ERRORLEVEL%"
echo.
if not "%toolkit_exit%"=="0" echo Monthly loop stopped with exit code %toolkit_exit%.
pause
exit /b %toolkit_exit%
