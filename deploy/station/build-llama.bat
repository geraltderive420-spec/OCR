@echo off
setlocal EnableExtensions
title Compilation de llama.cpp pour la RTX 3060 (sm_86)
rem ==========================================================================
rem  A lancer sur une machine CONNECTEE (pas sur la station) : compile llama-server
rem  avec le code natif de la RTX 3060 (architecture 86) + PTX 86 pour les cartes plus
rem  recentes, et 61 (GTX 10xx) pour pouvoir aussi tester sur une ancienne carte.
rem
rem  Prerequis : Visual Studio 2022 (outils C++), CUDA Toolkit 12.x, CMake.
rem  Usage :  build-llama.bat [dossier_des_sources_llama.cpp] [dossier_cuda_bin\nvcc.exe]
rem  Les sources sont clonees si le dossier n'existe pas ; le build est cree dans
rem  <sources>\build-station (le build habituel n'est pas touche).
rem  Puis :   export_station.bat --llama-dir <sources>\build-station\bin\Release
rem ==========================================================================
set "SRC=%~1"
if "%SRC%"=="" set "SRC=C:\LLAMA.CPP.CUDA\llama.cpp"
set "NVCC=%~2"
if "%NVCC%"=="" set "NVCC=C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.9/bin/nvcc.exe"
if not exist "%SRC%\CMakeLists.txt" (
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "%SRC%"
  if errorlevel 1 (
    echo [FAILED] Clonage impossible.
    pause
    exit /b 1
  )
)
cd /d "%SRC%"
cmake -S . -B build-station -G "Visual Studio 17 2022" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="61-real;86-real;86-virtual" -DCMAKE_CUDA_COMPILER="%NVCC%" -DLLAMA_CURL=OFF -DBUILD_SHARED_LIBS=ON -DGGML_CUDA_FA_ALL_QUANTS=OFF
if errorlevel 1 (
  echo [FAILED] Configuration CMake impossible.
  pause
  exit /b 1
)
cmake --build build-station --config Release --target llama-server -j 12
if errorlevel 1 (
  echo [FAILED] Compilation impossible.
  pause
  exit /b 1
)
rem Build processeur de secours (portable : AVX2, sans -march=native ; sert si le GPU est absent ou en panne).
cmake -S . -B build-cpu -G "Visual Studio 17 2022" -DGGML_CUDA=OFF -DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_AVX512=OFF -DLLAMA_CURL=OFF -DBUILD_SHARED_LIBS=ON
if errorlevel 1 (
  echo [FAILED] Configuration du build processeur impossible.
  pause
  exit /b 1
)
cmake --build build-cpu --config Release --target llama-server -j 12
if errorlevel 1 (
  echo [FAILED] Compilation du build processeur impossible.
  pause
  exit /b 1
)
echo.
echo SUCCESS - %CD%\build-station\bin\Release  et  %CD%\build-cpu\bin\Release
echo Puis : export_station.bat --llama-dir "%CD%\build-station\bin\Release" --llama-cpu-dir "%CD%\build-cpu\bin\Release"
pause
endlocal
