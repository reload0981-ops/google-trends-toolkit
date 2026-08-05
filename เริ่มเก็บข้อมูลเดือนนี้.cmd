@echo off
rem Operator entry point. Writes the canonical queue so the Controller dropdown
rem stays current, then copies it to the Desktop so the person running it never
rem has to browse into the repository folders.
rem Keep this file ASCII only: cmd.exe reads it with the OEM codepage.
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\toolkit.ps1" monthly-run -DesktopCopy %*
set "toolkit_exit=%ERRORLEVEL%"
echo.
if not "%toolkit_exit%"=="0" echo STOPPED with exit code %toolkit_exit%. Copy this window and send it to the maintainer.
pause
exit /b %toolkit_exit%
