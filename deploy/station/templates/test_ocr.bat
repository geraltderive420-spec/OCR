@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Test du moteur OCR
if not exist ".venv\Scripts\python.exe" (
  echo [ERREUR] Environnement absent. Lancez d'abord import_station.bat.
  pause
  exit /b 1
)
call "%~dp0ocr_env.bat"
cd /d "%~dp0app\backend"
echo.
echo   Lecture d'une page de test avec le vrai moteur (GPU, modeles, llama-server).
echo   Arretez l'application avant (demarrer_ocr.bat) : le moteur ne peut tourner qu'une fois.
echo.
"%~dp0.venv\Scripts\python.exe" -m app.cli selftest
set "RC=%ERRORLEVEL%"
echo.
pause
endlocal
exit /b %RC%
