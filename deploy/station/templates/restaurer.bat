@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Restauration
rem ==========================================================================
rem  Restaure une sauvegarde :   restaurer.bat archive.zip
rem  L'application doit etre ARRETEE. La base actuelle est d'abord copiee dans
rem  data\backups\avant-restauration-*.sqlite3.
rem ==========================================================================
if "%~1"=="" (
  echo Usage : restaurer.bat chemin\vers\ocr-AAAAMMJJ-HHMMSS.zip
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo [ERREUR] Environnement absent.
  pause
  exit /b 1
)
call "%~dp0ocr_env.bat"
curl -s -o nul --max-time 3 http://127.0.0.1:%OCR_API_PORT%/api/health
if not errorlevel 1 (
  echo [ERREUR] L'application tourne encore : arretez-la ^(Ctrl+C dans sa fenetre^) puis relancez.
  pause
  exit /b 1
)
echo ATTENTION : la base et les fichiers actuels vont etre remplaces par ceux de la sauvegarde.
set /p OK=Tapez OUI pour continuer :
if /i not "%OK%"=="OUI" exit /b 1
cd /d "%~dp0app\backend"
"%~dp0.venv\Scripts\python.exe" -m app.cli restore "%~1"
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" "%~dp0.venv\Scripts\python.exe" -m app.cli migrate
echo.
pause
endlocal
exit /b %RC%
