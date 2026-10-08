@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Pare-feu OCR
rem ==========================================================================
rem  Autorise en ENTREE les ports de l'application dans le pare-feu Windows
rem  (profils prive et domaine, pas public) : sans cela, les autres postes ne
rem  peuvent pas joindre l'application meme si elle ecoute sur 0.0.0.0.
rem
rem    ouvrir_pare_feu.bat            ouvre les ports
rem    ouvrir_pare_feu.bat supprimer  retire la regle
rem
rem  A lancer en administrateur (clic droit : Executer en tant qu'administrateur).
rem  Ports ouverts : interface 3050 et API 8085. llama.cpp (8091, 8092) reste
rem  ferme : seul le worker de ce poste l'appelle, et il n'a aucune authentification.
rem ==========================================================================
set "REGLE=OCR - entrant"
call "%~dp0ocr_env.bat"
set "PORTS=%OCR_FRONTEND_PORT%,%OCR_API_PORT%"

net session >nul 2>&1
if errorlevel 1 (
  echo [ERREUR] Droits administrateur requis : clic droit sur ce script, "Executer en tant qu'administrateur".
  pause
  exit /b 1
)
netsh advfirewall firewall delete rule name="%REGLE%" >nul 2>&1
if /i "%~1"=="supprimer" (
  echo Regle "%REGLE%" supprimee.
  pause
  exit /b 0
)
netsh advfirewall firewall add rule name="%REGLE%" dir=in action=allow protocol=TCP localport=%PORTS% profile=private,domain >nul
if errorlevel 1 (
  echo [ECHEC] Creation de la regle impossible.
  pause
  exit /b 1
)
echo Ports ouverts en entree (reseau prive et domaine) : %PORTS%
echo Adresse a communiquer aux autres postes : http://ADRESSE_IP_DE_CE_POSTE:%OCR_FRONTEND_PORT%
echo (le profil reseau "Public" reste ferme : passez le reseau en "Prive" si besoin).
pause
endlocal
