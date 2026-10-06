# Phase 0 — test de PaddleOCR-VL-1.6 (0,9 B) pour l'arabe

Date : 07/10/2026. Mesures sur les mêmes 12 pages que Surya (6 arabes : A2010001, A2020001 ; 6 françaises : F2010001, F2020001), GTX 1050 Ti 4 Go, GPU non partagé pendant les mesures. Rapports : `rapports/delave_paddlevl.json` (profil « délavé »), `rapports/propre_paddlevl.json` (pages propres). Aucun texte reconnu n'est stocké.

## Versions installées (environnement `D:\ocr_work\venv\paddlevl`, Python 3.10.11)

| Paquet | Version |
|---|---|
| paddleocr (`[doc-parser]`) | 3.7.0 (pipeline `PaddleOCRVL`, version par défaut `v1.6`) |
| paddlex | 3.7.2 |
| paddlepaddle (CPU) | 3.2.1 |
| torch / torchvision | 2.7.1+cu118 / 0.22.1+cu118 |
| transformers / accelerate | 5.19.0 / 1.15.0 |
| modèles | `PaddleOCR-VL-1.6` (lecture) + `PP-DocLayoutV3_safetensors` (mise en page) |

**Pascal (sm_61) : le moteur Paddle natif n'est pas utilisable** (la doc exige une capacité de calcul >= 7.0, et les roues `paddlepaddle-gpu` ne ciblent pas sm_61). Contournement : `engine="transformers"` (option de PaddleOCR 3.7) qui exécute **à la fois la mise en page et le VLM avec PyTorch**, dont la roue cu118 contient sm_61. `paddlepaddle` CPU ne sert alors qu'aux dépendances. Python 3.11 était annoncé par `py` mais le dossier est vide : 3.10 utilisé.

## Reproduire

```bash
# environnement : tous les caches sur D:
source /d/ocr_work/env.sh   # PIP_CACHE_DIR, HF_HOME, TMP/TEMP, PADDLE_PDX_CACHE_HOME=D:\ocr_work\paddlex,
                            # PADDLEX_HOME, XDG_CACHE_HOME, HF_HUB_CACHE, TORCH_HOME, PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
python310 -m venv D:\ocr_work\venv\paddlevl
pip install paddlepaddle==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install "paddleocr[doc-parser]"
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
pip install transformers accelerate rapidfuzz
# évaluation (PYTHONUTF8=1)
python tools/evaluate.py samples/degrades/delave_ref --backend paddlevl --reference samples/reference --sortie rapports/delave_paddlevl.json
python tools/evaluate.py samples/propres_ref        --backend paddlevl --reference samples/reference --sortie rapports/propre_paddlevl.json
```
Adaptateur : `tools/ocr_backend_paddlevl.py` (float16 par défaut ; `PADDLEVL_DTYPE`, `PADDLEVL_DEVICE`). Les modèles sont téléchargés au premier lancement dans `PADDLE_PDX_CACHE_HOME\official_models`. `env.sh` est dans `D:\ocr_work`, hors dépôt.

## Tailles (bundle hors ligne)

| Élément | Taille |
|---|---|
| Poids `PaddleOCR-VL-1.6` | 1,8 Go |
| Poids `PP-DocLayoutV3_safetensors` | 128 Mo |
| Environnement Python complet (torch 5,2 Go, paddle 0,4 Go) | 6,4 Go (cache pip : 3,0 Go) |

À comparer : Surya 2 GGUF = 1,27 Go + 205 Mo, sans environnement torch.

## Résultats (pages délavées, 12 pages)

| | PaddleOCR-VL-1.6 | Surya 2 | Surya 1 |
|---|---|---|---|
| Rappel mots arabe (pages 1-6) | **53,6 %** | 70,3 % | 71,2 % |
| Précision mots arabe | 57,8 % | 75,5 % | 73,1 % |
| Rappel mots français (7-12) | 81,5 % | 90,5 % | 89,2 % |
| Précision mots français | 86,2 % | 90,8 % | 87,7 % |
| Tableaux détectés (référence : 11) | 11 | 11 | 5 |
| Images détectées | 10 | 16 | 7 |
| Secondes / page | 74,9 (moyenne ; 45-70 hors page 2) | 93,2 | 162,6 |
| VRAM, pic total | 2 688 Mo | 2 130 Mo | 3 966 Mo |
| Chargement | 30 s | 8 s | 12 s |

Pages propres (PaddleOCR-VL) : arabe 54,0 % / précision 68,6 % ; français 86,0 % / 87,8 % ; 49,9 s/page ; 11 tableaux. Surya 1 sur les mêmes pages propres : arabe 73,0 %, français 92,4 %.

Le résultat est donc **le même sur pages propres et délavées** : la faiblesse sur l'arabe vient du modèle, pas de la dégradation.

## Problèmes rencontrés

- **Boucle de répétition** : sur la page arabe 2 (délavée), le modèle a produit 7 786 caractères (référence : 2 309) en 313 s, en 2 blocs seulement (un tableau qui se répète) : précision 15 %. Pas de garde-fou de longueur dans l'adaptateur ; `max_new_tokens` / `repetition_penalty` n'ont pas été essayés (pas de réglage au-delà d'un essai raisonnable).
- Pages arabes 5 et 6 : rappel 32 % et 46 % (la page 5 n'a que 212 caractères reconnus pour ~494 attendus).
- Pas de score de confiance : `confiance` est vide dans le rapport (impossible de cibler la correction LLM comme avec Surya).
- Le premier essai a été fait avec deux processus concurrents (mesure de 425 s non retenue) ; les temps du tableau viennent d'exécutions seules sur le GPU.
- Avertissement transformers « tied weights » au chargement : sans effet observé.
- Le disque C: n'a pas été touché (19 Go libres tout au long) ; `~/.paddlex` (558 Mo, antérieur à ce test) n'a pas été utilisé.

## Recommandation

**Abandonner PaddleOCR-VL-1.6 comme lecteur d'arabe** : sur 12 pages il lit nettement moins de mots arabes que Surya (54 % contre 70 %, soit -17 points) et moins de mots français (-9 points), sans gain de vitesse décisif (65-75 s contre 93 s), avec un environnement torch de 6,4 Go et un risque de boucle de génération. Sa mise en page est correcte (11 tableaux sur 11) mais Surya 2 fait aussi bien. Ne pas non plus l'adopter pour la seule mise en page : pas de gain, et les étiquettes (41 `PageHeader` Surya contre 26) restent à vérifier de part et d'autre. Garder **Surya 2** ; l'arabe reste à améliorer par un autre moyen (dots.ocr, prétraitement, relecture).

## Limites de la mesure

12 pages seulement (6 arabes), issues de 4 documents d'un même type (journaux officiels), donc écarts de quelques points non significatifs ; ici l'écart est de 17 points sur l'arabe. Référence = couche texte du PDF (rappel / précision de mots insensibles à l'ordre ; CER ignoré). Pages « propres » = rendu numérique, pas de vrais scans anciens. Un seul réglage testé (float16, torch cu118, valeurs d'inférence par défaut) ; les temps dépendent de l'architecture Pascal (le fp16 y est lent) et ne se transposent pas à la RTX 3060.
