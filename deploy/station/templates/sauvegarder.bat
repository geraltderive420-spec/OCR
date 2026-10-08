@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Sauvegarde
rem ==========================================================================
rem  Sauvegarde a chaud (l'application peut tourner) : base + fichiers deposes
rem  + Word produits, dans une archive ZIP.   sauvegarder.bat [dossier]
rem  Par defaut : le dossier "sauvegardes" a cote des donnees. Mettez-les sur un
rem  AUTRE disque (cle USB chiffree ou disque externe), jamais seulement ici.
rem ==========================================================================
if not exist ".venv\Scripts\python.exe" (
  echo [ERREUR] Environnement absent.
  pause
  exit /b 1
)
call "%~dp0ocr_env.bat"
set "DEST=%~1"
if "%DEST%"=="" set "DEST=%OCR_DATA_DIR%\sauvegardes"
cd /d "%~dp0app\backend"
"%~dp0.venv\Scripts\python.exe" -m app.cli backup "%DEST%" --garder 30
set "RC=%ERRORLEVEL%"
echo.
pause
endlocal
exit /b %RC%
