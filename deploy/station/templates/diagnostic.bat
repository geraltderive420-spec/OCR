@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Diagnostic
if not exist ".venv\Scripts\python.exe" (
  echo [ERREUR] Environnement absent. Lancez d'abord import_station.bat.
  pause
  exit /b 1
)
call "%~dp0ocr_env.bat"
cd /d "%~dp0app\backend"
echo.
"%~dp0.venv\Scripts\python.exe" -m app.cli check
set "RC=%ERRORLEVEL%"
echo.
pause
endlocal
exit /b %RC%
