#!/usr/bin/env bash
# Surya 2 seul (llama.cpp). Usage : tools/run_surya2.sh ENTREE REFERENCES SORTIE.json
# Parametre : LLAMA_CPP_DIR = dossier Release de llama.cpp (contient llama-server.exe) ; LLAMA_CPP_NGL=0 force le processeur.
set -u
ENTREE="${1:?entree}"; REF="${2:?references}"; SORTIE="${3:?sortie}"
cd "$(dirname "$0")/.."
export LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-C:/LLAMA.CPP.CUDA/llama.cpp/build/bin/Release}"
SENT="$HOME/.cache/datalab/surya/llamacpp_server.json"
llama_pids() { tasklist 2>/dev/null | grep -i "llama-server.exe" | awk '{print $2}' | sort; }
rm -f "$SENT" "${SENT%.json}.lock"
AVANT="$(llama_pids)"
SURYA_INFERENCE_BACKEND=llamacpp SURYA_INFERENCE_PARALLEL=1 \
  ./.venv-surya2/Scripts/python.exe tools/evaluate.py "$ENTREE" --backend surya2 --reference "$REF" \
  --sans-echauffement --sortie "$SORTIE" > "${SORTIE%.json}.log" 2>&1
echo "surya2 termine (code $?)"
# Surya 2 ne sait pas arreter son serveur sous Windows : on ne tue que celui qu'il a cree.
for pid in $(comm -13 <(echo "$AVANT") <(llama_pids)); do taskkill //F //PID "$pid" > /dev/null 2>&1; done
rm -f "$SENT" "${SENT%.json}.lock"
