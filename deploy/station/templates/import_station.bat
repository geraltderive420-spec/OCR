@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Installation OCR - Station (100%% hors-ligne)

rem ==========================================================================
rem  INSTALLATION SUR LA STATION HORS-LIGNE
rem
rem  Aucun acces internet n'est requis, ni pendant l'installation ni ensuite.
rem  Le script s'arrete au premier probleme bloquant (FAILED) sans rien lancer.
rem  Le relancer apres correction est sans danger : tout ce qui est deja en
rem  place est reutilise (python, environnement, dependances, base, comptes).
rem
rem  Option :  import_station.bat /rapide   integrite : presence et taille seulement
rem ==========================================================================

set "KIT=%~dp0"
set "PYTHONUTF8=1"
chcp 65001 >nul 2>&1
set "LOG=%KIT%logs\import.log"
if not exist "%KIT%logs" mkdir "%KIT%logs"
echo ========================================================== > "%LOG%"
echo Installation OCR Station - %DATE% %TIME% >> "%LOG%"

set "MODE_INTEGRITE="
if /i "%~1"=="/rapide" set "MODE_INTEGRITE=--rapide"

echo.
echo ==========================================================================
echo   INSTALLATION OCR - STATION DE PRODUCTION (100%% hors-ligne)
echo ==========================================================================
echo.

rem --------------------------------------------------------------------------
rem  1/8  Python 3.13 x64 : detection, sinon installation depuis le colis
rem --------------------------------------------------------------------------
echo [1/8] Python 3.13 x64...
call :find_py
if defined PYEXE goto :py_ok

echo   Aucun Python 3.13 sur cette machine.
if not exist "%KIT%python\python-setup.exe" (
  echo   [FAILED] Installeur absent du colis : python\python-setup.exe
  echo            Le dossier python\ n'a pas ete copie.
  goto :failed
)
echo   Installation silencieuse depuis le colis (sans acces internet)...
echo   Portee : utilisateur courant, aucun droit administrateur necessaire.
"%KIT%python\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 Include_doc=0 AssociateFiles=0 >> "%LOG%" 2>&1
if errorlevel 1 (
  echo   [FAILED] L'installeur Python a retourne une erreur. Detail : %LOG%
  goto :failed
)
set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PYEXE%" set "PYEXE="
if not defined PYEXE call :find_py
if not defined PYEXE (
  echo   [FAILED] Python installe mais introuvable dans cette session.
  echo            Fermez cette fenetre, ouvrez-en une nouvelle et relancez import_station.bat.
  goto :failed
)
echo   Installe : %PYEXE%

:py_ok
call :getver "%PYEXE%"
if not "%PYVER%"=="3.13" (
  echo   [FAILED] Python 3.13 x64 requis, detecte : %PYVER%
  echo            Les roues du colis sont compilees pour cp313 uniquement.
  goto :failed
)
call :getbits "%PYEXE%"
if not "%PYBITS%"=="64" (
  echo   [FAILED] Python 64 bits requis, detecte : %PYBITS% bits.
  goto :failed
)
echo   %PYEXE%  (3.13 / 64 bits)
echo   [OK]

rem --------------------------------------------------------------------------
rem  2/8  Runtime Microsoft Visual C++ 2015-2022 x64 (torch et llama-server)
rem --------------------------------------------------------------------------
echo.
echo [2/8] Runtime Microsoft Visual C++ 2015-2022 x64...
if exist "%SystemRoot%\System32\vcruntime140_1.dll" (
  echo   Deja present.
  echo   [OK]
  goto :vc_done
)
if not exist "%KIT%installers\vc_redist.x64.exe" (
  echo   [ATTENTION] Runtime absent et installeur non fourni. Si torch ou llama-server
  echo               echoue plus loin sur une erreur de DLL, installez le redistribuable.
  goto :vc_done
)
net session >nul 2>&1
if errorlevel 1 (
  echo   [ATTENTION] Runtime absent, mais les droits administrateur sont requis pour
  echo               l'installer. Relancez ce script en tant qu'administrateur si
  echo               l'etape 5 echoue sur une erreur de DLL.
  goto :vc_done
)
echo   Installation silencieuse du redistribuable...
"%KIT%installers\vc_redist.x64.exe" /install /quiet /norestart >> "%LOG%" 2>&1
if errorlevel 3011 goto :vc_warn
if errorlevel 3010 (
  echo   Installe. Un redemarrage sera necessaire avant le premier lancement.
  echo   [OK]
  goto :vc_done
)
if errorlevel 1 goto :vc_warn
echo   [OK]
goto :vc_done

:vc_warn
echo   [ATTENTION] L'installation du runtime a retourne une erreur (voir %LOG%).
echo               On continue : il est peut-etre deja present sous une autre version.

:vc_done

rem --------------------------------------------------------------------------
rem  3/8  Integrite du colis
rem --------------------------------------------------------------------------
echo.
echo [3/8] Controle d'integrite du colis...
if defined MODE_INTEGRITE echo   Mode rapide : presence et taille des fichiers.
if not defined MODE_INTEGRITE echo   SHA-256 complet - comptez 2 a 6 minutes pour plusieurs Go.
"%PYEXE%" "%KIT%verifier_integrite.py" --kit "%KIT%." %MODE_INTEGRITE%
if errorlevel 1 (
  echo   [FAILED] Le colis est incomplet ou abime. Rien n'a ete installe.
  goto :failed
)

rem --------------------------------------------------------------------------
rem  4/8  Environnement virtuel
rem --------------------------------------------------------------------------
echo.
echo [4/8] Environnement virtuel local...
if exist "%KIT%.venv\Scripts\python.exe" (
  echo   .venv deja present, reutilise.
) else (
  "%PYEXE%" -m venv "%KIT%.venv" >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo   [FAILED] Creation de l'environnement virtuel impossible. Detail : %LOG%
    goto :failed
  )
)
set "VPY=%KIT%.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo   [FAILED] .venv incomplet : %VPY% absent. Supprimez le dossier .venv et relancez.
  goto :failed
)
echo   [OK]

rem --------------------------------------------------------------------------
rem  5/8  Dependances Python, 100%% hors-ligne
rem --------------------------------------------------------------------------
echo.
echo [5/8] Installation des dependances (hors-ligne, depuis .\wheels)...
echo   torch CUDA 12.6 inclus (~2,5 Go) : comptez plusieurs minutes.
"%VPY%" "%KIT%installer_dependances.py" --kit "%KIT%."
if errorlevel 1 (
  echo   [FAILED] Installation des dependances incomplete ^(detail ci-dessus et logs\pip.log^).
  goto :failed
)

rem --------------------------------------------------------------------------
rem  6/8  Modeles, llama.cpp, configuration du poste
rem --------------------------------------------------------------------------
echo.
echo [6/8] Modeles, llama.cpp et configuration...
call "%KIT%ocr_env.bat"
if not exist "%KIT%llama\llama-server.exe" (
  echo   [FAILED] llama-server.exe absent : dossier llama\ non copie.
  goto :failed
)
if not exist "%KIT%models\hf\hub" (
  echo   [FAILED] Modeles absents : dossier models\hf\ non copie.
  goto :failed
)
if not exist "%OCR_DATA_DIR%" mkdir "%OCR_DATA_DIR%" >nul 2>&1
if not exist "%OCR_DATA_DIR%" (
  echo   [FAILED] Dossier de donnees impossible a creer : %OCR_DATA_DIR%
  goto :failed
)
"%VPY%" "%KIT%configurer.py" "%KIT%."
if errorlevel 1 (
  echo   [FAILED] Configuration impossible.
  goto :failed
)
echo   Donnees      : %OCR_DATA_DIR%
echo   llama.cpp    : %OCR_LLAMA_CPP_DIR%
echo   Modeles      : %HF_HOME%
echo   [OK]

rem --------------------------------------------------------------------------
rem  7/8  Base de donnees (SQLite) et premier administrateur
rem --------------------------------------------------------------------------
echo.
echo [7/8] Base de donnees et compte administrateur...
pushd "%KIT%app\backend"
"%VPY%" -m app.cli migrate >> "%LOG%" 2>&1
if errorlevel 1 (
  popd
  echo   [FAILED] Creation de la base impossible. Detail : %LOG%
  goto :failed
)
"%VPY%" -m app.cli has-users >nul 2>&1
if not errorlevel 1 (
  echo   Comptes existants conserves.
  goto :admin_done
)
echo   Aucun compte : creation de l'administrateur.
rem Installation sans surveillance : OCR_ADMIN_USER et OCR_ADMIN_PASSWORD definis avant le lancement.
set "ADMIN=%OCR_ADMIN_USER%"
if defined ADMIN goto :admin_create
set /p ADMIN=  Identifiant de l'administrateur : 
if not defined ADMIN goto :admin_fail
:admin_create
"%VPY%" -m app.cli create-admin %ADMIN%
if errorlevel 1 goto :admin_fail
:admin_done
popd
echo   [OK]
goto :step8

:admin_fail
popd
echo   [FAILED] Creation de l'administrateur impossible (identifiant vide ou mot de passe trop faible).
echo            Relancez import_station.bat ou utilisez creer_admin.bat.
goto :failed

:step8

rem --------------------------------------------------------------------------
rem  8/8  Verification finale (aucun acces reseau)
rem --------------------------------------------------------------------------
echo.
echo [8/8] Verification de l'installation...
pushd "%KIT%app\backend"
"%VPY%" -m app.cli check
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
  echo   [FAILED] Le diagnostic signale un probleme bloquant ^(KO ci-dessus^).
  goto :failed
)

echo.
echo ==========================================================================
echo   SUCCESS - Station prete.
echo.
echo     1. test_ocr.bat        lit une page de test avec le vrai moteur (2 a 4 min la
echo                            premiere fois) : A FAIRE avant la mise en service
echo     2. demarrer_ocr.bat    lance l'application  -^>  http://127.0.0.1:3050
echo     3. demarrage_auto.bat installer   lancement a l'ouverture de session
echo.
echo   Ollama (correction par l'IA) et le pilote NVIDIA ne sont pas dans le colis :
echo   l'application fonctionne sans Ollama, sans correction automatique.
echo ==========================================================================
echo.
pause
endlocal
exit /b 0

rem ==========================================================================
rem  Sous-routines
rem ==========================================================================

:find_py
set "PYEXE="
py -3.13 -c "import sys;print(sys.executable)" > "%KIT%logs\pyexe.txt" 2>nul
if exist "%KIT%logs\pyexe.txt" set /p PYEXE=<"%KIT%logs\pyexe.txt"
del "%KIT%logs\pyexe.txt" >nul 2>&1
if defined PYEXE if not exist "%PYEXE%" set "PYEXE="
if defined PYEXE exit /b 0
for /f "delims=" %%P in ('where python 2^>nul') do (
  if not defined PYEXE call :try_py "%%P"
)
exit /b 0

:try_py
call :getver "%~1"
if "%PYVER%"=="3.13" set "PYEXE=%~1"
exit /b 0

:getver
rem Le resultat transite par un fichier : un chemin entre guillemets dans un for /f est mal interprete.
set "PYVER="
if not exist "%~1" exit /b 1
"%~1" -c "import sys;print('%%d.%%d'%%sys.version_info[:2])" > "%KIT%logs\pyver.txt" 2>nul
if exist "%KIT%logs\pyver.txt" set /p PYVER=<"%KIT%logs\pyver.txt"
del "%KIT%logs\pyver.txt" >nul 2>&1
exit /b 0

:getbits
set "PYBITS="
if not exist "%~1" exit /b 1
"%~1" -c "import struct;print(struct.calcsize('P')*8)" > "%KIT%logs\pybits.txt" 2>nul
if exist "%KIT%logs\pybits.txt" set /p PYBITS=<"%KIT%logs\pybits.txt"
del "%KIT%logs\pybits.txt" >nul 2>&1
exit /b 0

:failed
echo.
echo ==========================================================================
echo   FAILED - Installation interrompue. L'application n'a pas ete lancee.
echo   Journal : %LOG%     (relancer import_station.bat apres correction est sans danger)
echo ==========================================================================
echo.
pause
endlocal
exit /b 1
