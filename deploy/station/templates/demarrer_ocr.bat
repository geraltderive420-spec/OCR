@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Numerisation OCR

rem ==========================================================================
rem  Demarre l'application : base (migration automatique), API et worker OCR.
rem  Les deux processus sont relances s'ils s'arretent ; Ctrl+C arrete tout
rem  proprement (llama-server compris, la memoire du GPU est liberee).
rem ==========================================================================
if not exist ".venv\Scripts\python.exe" (
  echo [ERREUR] Environnement absent. Lancez d'abord import_station.bat.
  pause
  exit /b 1
)
call "%~dp0ocr_env.bat"
cd /d "%~dp0app\backend"
echo.
echo   Application : http://%OCR_API_HOST%:%OCR_FRONTEND_PORT%    (Ctrl+C pour arreter)
echo   API         : http://%OCR_API_HOST%:%OCR_API_PORT%     llama.cpp : %OCR_LLAMA_PORT%
echo   Journaux    : %OCR_DATA_DIR%\logs
echo.
"%~dp0.venv\Scripts\python.exe" -m app.serve
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo   L'application s'est arretee avec le code %RC%. Voir %OCR_DATA_DIR%\logs\serve.log
  echo   Code 2 : migration de la base impossible. Code 4 : un port est deja utilise
  echo   ^(8085, 3050^) : l'application tourne peut-etre deja.
  pause
)
endlocal
exit /b %RC%
