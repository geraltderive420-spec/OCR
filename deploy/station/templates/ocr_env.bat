@echo off
rem ==========================================================================
rem  Variables d'environnement de la station (appele par les autres scripts).
rem
rem  Tout est derive de l'emplacement de ce dossier : le deplacer ne casse rien.
rem  Hors ligne strict : aucun client (Hugging Face, transformers) ne peut tenter
rem  de telecharger quoi que ce soit.
rem ==========================================================================
set "OCR_KIT=%~dp0"

rem --- Donnees (base SQLite, fichiers, journaux) -----------------------------
rem Par defaut dans le colis. Pour les mettre ailleurs (autre disque), decommentez
rem et adaptez la ligne suivante AVANT le premier demarrage :
rem set "OCR_DATA_ROOT=D:\ocr_donnees"
if not defined OCR_DATA_ROOT set "OCR_DATA_ROOT=%OCR_KIT%data"
set "OCR_DATA_DIR=%OCR_DATA_ROOT%"

rem --- llama.cpp : build CUDA et build processeur de secours ------------------
set "OCR_LLAMA_CPP_DIR=%OCR_KIT%llama"
set "OCR_LLAMA_CPP_CPU_DIR="
if exist "%OCR_KIT%llama-cpu\llama-server.exe" set "OCR_LLAMA_CPP_CPU_DIR=%OCR_KIT%llama-cpu"
set "PATH=%OCR_KIT%llama;%PATH%"

rem --- Modeles locaux ----------------------------------------------------------
set "HF_HOME=%OCR_KIT%models\hf"
set "MODEL_CACHE_DIR=%OCR_KIT%models\datalab"
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "HF_HUB_DISABLE_TELEMETRY=1"
set "DO_NOT_TRACK=1"

rem --- Ports et adresses d'ecoute -----------------------------------------------
rem  Interface  0.0.0.0:3050   (adresse a ouvrir dans le navigateur, depuis tout le reseau)
rem  API        0.0.0.0:8085
rem  llama.cpp  127.0.0.1:8091 (second lecteur : 8092) - appele par le worker seulement
rem  Ollama     127.0.0.1:11434 - appele par l'application seulement
rem Imposes ici SANS condition : une variable OCR_* heritee de Windows ou d'une ancienne
rem installation ne peut pas les remplacer. Pour changer un port : modifier ces lignes.
rem Acces local seulement : remplacer 0.0.0.0 par 127.0.0.1.
rem Pare-feu Windows : ouvrir_pare_feu.bat (administrateur) autorise 3050 et 8085 en entree.
set "OCR_API_HOST=0.0.0.0"
set "OCR_API_PORT=8085"
set "OCR_FRONTEND_HOST=0.0.0.0"
set "OCR_FRONTEND_PORT=3050"
set "OCR_LLAMA_HOST=127.0.0.1"
set "OCR_LLAMA_PORT=8091"

rem --- Console en UTF-8 (accents des journaux et des noms de fichiers) --------
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul 2>&1
