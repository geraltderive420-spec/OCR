@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Export OCR - colis pour la station 100%% hors-ligne

rem ==========================================================================
rem  EXPORT EN UN CLIC (machine SOURCE, celle qui a internet)
rem
rem  Fabrique  transfert_station\  : un colis autonome pour la station de
rem  production (Windows 11, RTX 3060 12 Go, coupee du reseau) : application,
rem  installeur Python 3.13, runtime Visual C++, toutes les dependances Python
rem  en roues binaires (torch CUDA 12.6 compris), modeles OCR, llama-server,
rem  et les empreintes SHA-256 pour controler la copie a l'arrivee.
rem
rem  Ne sont PAS embarques (deja sur la station) : le pilote NVIDIA, Ollama.
rem
rem  Variables facultatives :
rem     OCR_EXPORT_DIR   dossier du colis (defaut : transfert_station ici). Il
rem                      faut ~10 Go libres ; le cache de preparation est
rem                      cree a cote (liens physiques, meme volume).
rem  Options (transmises a deploy\station\export_kit.py) :
rem     --skip-models   --skip-wheels   --skip-hash
rem     --llama-dir D   build CUDA de llama.cpp (defaut : OCR_LLAMA_CPP_DIR de backend\.env)
rem     --llama-cpu-dir D   build processeur de secours
rem     --maj           colis de mise a jour leger (maj_station\)
rem ==========================================================================

if not exist "logs" mkdir "logs"
set "LOG=%~dp0logs\export_station.log"

echo.
echo ==========================================================================
echo   EXPORT OCR - colis pour la station de production hors-ligne
echo ==========================================================================
echo.

set "PYEXE=%~dp0.venv-surya2\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=%~dp0backend\.venv\Scripts\python.exe"
if not exist "%PYEXE%" (
  set "PYEXE="
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYEXE set "PYEXE=%%P"
  )
)
if not defined PYEXE (
  echo   [FAILED] Aucun interpreteur Python utilisable sur cette machine.
  goto :failed
)
echo   Interpreteur source : %PYEXE%

set "OUTARG="
if defined OCR_EXPORT_DIR set OUTARG=--out "%OCR_EXPORT_DIR%"

"%PYEXE%" "%~dp0deploy\station\export_kit.py" %OUTARG% %* 2>&1
if errorlevel 1 goto :failed

echo.
echo ==========================================================================
echo   SUCCESS - Colis genere.
echo.
echo     1. Copier le dossier du colis sur un disque ou une cle USB
echo     2. Sur la station : le recopier sur le disque local (ex. C:\OCR)
echo     3. Lancer verifier_colis.bat, puis import_station.bat, puis test_ocr.bat
echo ==========================================================================
echo.
pause
endlocal
exit /b 0

:failed
echo.
echo ==========================================================================
echo   FAILED - Export interrompu. Le colis peut etre incomplet.
echo ==========================================================================
echo.
pause
endlocal
exit /b 1
