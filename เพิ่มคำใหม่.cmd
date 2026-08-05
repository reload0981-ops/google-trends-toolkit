@echo off
rem Operator entry point for adding one keyword. Asks the two questions a
rem person has to answer, then runs the whole loop: queue, collect, screen,
rem set the tier, ingest.
rem Keep this file ASCII only: cmd.exe reads it with the OEM codepage.
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\toolkit.ps1" add-keyword
set "toolkit_exit=%ERRORLEVEL%"
echo.
if not "%toolkit_exit%"=="0" echo STOPPED with exit code %toolkit_exit%. Copy this window and send it to the maintainer.
pause
exit /b %toolkit_exit%
