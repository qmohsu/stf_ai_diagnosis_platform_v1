@echo off
setlocal

set "LAUNCHER_SCRIPT=%~dp0scripts\start-obd-expert.ps1"

if not exist "%LAUNCHER_SCRIPT%" (
  echo OBD Expert launcher script was not found:
  echo %LAUNCHER_SCRIPT%
  echo.
  pause
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER_SCRIPT%"
set "LAUNCHER_EXIT_CODE=%ERRORLEVEL%"

if not "%LAUNCHER_EXIT_CODE%"=="0" (
  echo.
  pause
)

exit /b %LAUNCHER_EXIT_CODE%
