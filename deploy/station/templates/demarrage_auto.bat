@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Demarrage automatique
rem ==========================================================================
rem  demarrage_auto.bat installer   : l'application demarre a l'ouverture de session
rem  demarrage_auto.bat supprimer   : retire le demarrage automatique
rem  (tache planifiee de l'utilisateur courant, fenetre reduite ; aucun droit admin)
rem ==========================================================================
set "TACHE=OCR-Numerisation"
if /i "%~1"=="installer" goto :install
if /i "%~1"=="supprimer" goto :remove
echo Usage : demarrage_auto.bat installer ^| supprimer
exit /b 1

:install
schtasks /Create /TN "%TACHE%" /TR "cmd /c start \"OCR\" /min \"%~dp0demarrer_ocr.bat\"" /SC ONLOGON /F >nul
if errorlevel 1 (
  echo [ECHEC] Creation de la tache impossible.
  pause
  exit /b 1
)
echo Demarrage automatique installe : l'application se lancera a chaque ouverture de session.
pause
exit /b 0

:remove
schtasks /Delete /TN "%TACHE%" /F >nul 2>&1
echo Demarrage automatique supprime.
pause
exit /b 0
