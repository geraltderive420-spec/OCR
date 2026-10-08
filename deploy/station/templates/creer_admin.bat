@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Creer un administrateur
if not exist ".venv\Scripts\python.exe" (
  echo [ERREUR] Environnement absent. Lancez d'abord import_station.bat.
  pause
  exit /b 1
)
call "%~dp0ocr_env.bat"
cd /d "%~dp0app\backend"
set "NOM=%~1"
if "%NOM%"=="" set /p NOM=Identifiant de l'administrateur :
if "%NOM%"=="" exit /b 1
"%~dp0.venv\Scripts\python.exe" -m app.cli migrate
"%~dp0.venv\Scripts\python.exe" -m app.cli create-admin %NOM%
echo.
pause
endlocal
