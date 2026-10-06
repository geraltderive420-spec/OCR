#!/usr/bin/env bash
# Lance Surya 2 puis Surya 1 sur les mêmes pages, l'un après l'autre (le GPU ne supporte pas les deux ensemble).
# Usage : tools/run_comparatif.sh samples/degrades/delave_ref samples/reference rapports/<prefixe>
set -u
ENTREE="${1:?dossier-images}"; REF="${2:?dossier-references}"; PREFIXE="${3:?prefixe}"
cd "$(dirname "$0")/.."

llama_pids() { tasklist 2>/dev/null | grep -i "llama-server.exe" | awk '{print $2}' | sort; }

SENTINELLE="$HOME/.cache/datalab/surya/llamacpp_server.json"
# Surya garde la trace de son serveur ici et tente de le reutiliser ; une trace perimee donne des "Connection error".
rm -f "$SENTINELLE" "${SENTINELLE%.json}.lock"
AVANT="$(llama_pids)"
echo "== Surya 2 (llama.cpp) =="
LLAMA_CPP_NO_MMPROJ_OFFLOAD=true SURYA_INFERENCE_BACKEND=llamacpp SURYA_INFERENCE_PARALLEL=1 \
  ./.venv-surya2/Scripts/python.exe tools/evaluate.py "$ENTREE" --backend surya2 --reference "$REF" \
  --sans-echauffement --sortie "${PREFIXE}_surya2.json" > "${PREFIXE}_surya2.log" 2>&1
echo "surya2 terminé (code $?)"
# Surya 2 ne sait pas toujours arrêter son llama-server sous Windows : on tue seulement les nouveaux.
for pid in $(comm -13 <(echo "$AVANT") <(llama_pids)); do taskkill //F //PID "$pid" > /dev/null 2>&1; done
rm -f "$SENTINELLE" "${SENTINELLE%.json}.lock"
sleep 3

echo "== Surya 1 (en processus) =="
python tools/evaluate.py "$ENTREE" --backend surya1 --reference "$REF" \
  --sortie "${PREFIXE}_surya1.json" > "${PREFIXE}_surya1.log" 2>&1
echo "surya1 terminé (code $?)"
