@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Verification du colis OCR

rem ==========================================================================
rem  Controle d'integrite du colis, a lancer apres la copie sur la station
rem  (ou a tout moment, pour verifier qu'un dossier n'a pas ete abime).
rem
rem  Utilise le python du venv s'il existe, sinon le python systeme : le
rem  script ne depend que de la bibliotheque standard, donc il fonctionne
rem  avant meme l'installation.
rem
rem  Options :  verifier_colis.bat /rapide   presence et taille seulement
rem ==========================================================================

set "KIT=%~dp0"
if not exist "%KIT%logs" mkdir "%KIT%logs"

set "MODE="
if /i "%~1"=="/rapide" set "MODE=--rapide"

set "PYEXE="
if exist "%KIT%.venv\Scripts\python.exe" set "PYEXE=%KIT%.venv\Scripts\python.exe"
if not defined PYEXE (
  py -3.13 -c "import sys;print(sys.executable)" > "%KIT%logs\pyexe.txt" 2>nul
  if exist "%KIT%logs\pyexe.txt" set /p PYEXE=<"%KIT%logs\pyexe.txt"
  del "%KIT%logs\pyexe.txt" >nul 2>&1
)
if defined PYEXE if not exist "%PYEXE%" set "PYEXE="
if not defined PYEXE (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYEXE set "PYEXE=%%P"
  )
)
if not defined PYEXE (
  echo.
  echo   [ERREUR] Aucun Python trouve : lancez d'abord import_station.bat,
  echo            qui installe l'interpreteur fourni par le colis.
  echo.
  pause
  endlocal
  exit /b 1
)

echo.
echo ==========================================================================
echo   VERIFICATION DU COLIS OCR
echo ==========================================================================
echo.
"%PYEXE%" "%KIT%verifier_integrite.py" --kit "%KIT%." %MODE%
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo ==========================================================================
  echo   FAILED - Colis incomplet. Recopiez-le en entier depuis la source.
  echo ==========================================================================
) else (
  echo ==========================================================================
  echo   SUCCESS - Colis complet et intact.
  echo ==========================================================================
)
echo.
pause
endlocal
exit /b %RC%
