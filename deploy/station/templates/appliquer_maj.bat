@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Mise a jour OCR - Station

rem ==========================================================================
rem  MISE A JOUR LEGERE (code + interface + dependances modifiees)
rem
rem    appliquer_maj.bat  C:\OCR        (dossier d'installation de la station)
rem
rem  Verifie le colis, exige l'application arretee, SAUVEGARDE la base, remplace
rem  le code (le .env et les donnees sont conserves), installe les dependances
rem  changees, migre la base, verifie. En cas d'echec, restaurez la sauvegarde
rem  (restaurer.bat) : elle est faite avant toute modification.
rem ==========================================================================
set "MAJ=%~dp0"
set "KIT=%~1"
if "%KIT%"=="" set /p KIT=Dossier d'installation de la station (ex. C:\OCR) :
if "%KIT%"=="" goto :usage
for %%I in ("%KIT%") do set "KIT=%%~fI"
set "VPY=%KIT%\.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo [FAILED] Aucune installation trouvee dans %KIT% ^(.venv absent^).
  goto :failed
)
call "%KIT%\ocr_env.bat"

echo.
echo [1/6] Controle d'integrite du colis de mise a jour...
"%VPY%" "%MAJ%verifier_integrite.py" --kit "%MAJ%."
if errorlevel 1 (
  echo [FAILED] Colis incomplet ou abime : recopiez-le. Rien n'a ete modifie.
  goto :failed
)

echo.
echo [2/6] L'application doit etre arretee...
curl -s -o nul --max-time 3 http://127.0.0.1:%OCR_API_PORT%/api/health
if not errorlevel 1 (
  echo [FAILED] L'application tourne encore : arretez-la ^(Ctrl+C dans sa fenetre^) puis relancez.
  goto :failed
)
echo   [OK]

echo.
echo [3/6] Sauvegarde de la base et des fichiers avant mise a jour...
pushd "%KIT%\app\backend"
"%VPY%" -m app.cli backup "%OCR_DATA_DIR%\sauvegardes" --garder 30
if errorlevel 1 (
  popd
  echo [FAILED] Sauvegarde impossible : rien n'a ete modifie.
  goto :failed
)
popd

echo.
echo [4/6] Remplacement du code...
robocopy "%MAJ%app" "%KIT%\app" /E /XF .env /XD data __pycache__ /NFL /NDL /NJH /NJS >nul
if errorlevel 8 (
  echo [FAILED] Copie du code impossible ^(fichiers verrouilles ?^).
  goto :failed
)
echo   [OK]

echo.
echo [5/6] Dependances...
if exist "%MAJ%wheels" (
  "%VPY%" "%MAJ%installer_dependances.py" --kit "%MAJ%." --wheels "%MAJ%wheels" --lock "%MAJ%requirements.lock.txt" --torch "%MAJ%aucun.txt"
  if errorlevel 1 (
    echo [FAILED] Installation des dependances incomplete. Restaurez avec restaurer.bat si necessaire.
    goto :failed
  )
) else (
  echo   Aucune dependance modifiee.
)
copy /y "%MAJ%requirements.lock.txt" "%KIT%\requirements.lock.txt" >nul

echo.
echo [6/6] Migration de la base et verification...
pushd "%KIT%\app\backend"
"%VPY%" -m app.cli migrate
if errorlevel 1 (
  popd
  echo [FAILED] Migration impossible : restaurez la sauvegarde avec restaurer.bat.
  goto :failed
)
"%VPY%" -m app.cli check
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
  echo [FAILED] Le diagnostic signale un probleme bloquant ^(KO ci-dessus^).
  goto :failed
)

echo.
echo ==========================================================================
echo   SUCCESS - Mise a jour appliquee. Relancez demarrer_ocr.bat.
echo ==========================================================================
pause
endlocal
exit /b 0

:usage
echo Usage : appliquer_maj.bat dossier_d_installation
pause
exit /b 1

:failed
echo.
echo ==========================================================================
echo   FAILED - Mise a jour interrompue ^(la sauvegarde, si faite, est dans
echo   data\sauvegardes^).
echo ==========================================================================
pause
endlocal
exit /b 1
