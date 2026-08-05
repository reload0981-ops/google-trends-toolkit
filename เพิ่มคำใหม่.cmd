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
rem 9 means a guard stopped on purpose and already explained itself above.
if "%toolkit_exit%"=="0" goto :done
if "%toolkit_exit%"=="9" goto :done
echo STOPPED with exit code %toolkit_exit%. Copy this window and send it to the maintainer.
:done
pause
exit /b %toolkit_exit%
