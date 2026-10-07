# Phase 0 : rapport de validation technique (07/10/2026)

Machine de mesure : GTX 1050 Ti 4 Go (Pascal, sm_61), i7-8750H. **Les vitesses et la VRAM de la RTX 3060 de production sont
à mesurer** (voir `REPRISE.md`). Échantillons : 10 numéros du Journal officiel algérien (public, français et arabe,
1965 à 2020), 12 pages dont 6 arabes et 6 françaises, dégradées par `tools/degrade.py` (profil « délavé »). Mesure principale :
**rappel / précision des mots** contre la couche texte du PDF (le CER de page entière dépend de l'ordre du texte et n'est pas fiable
ici). Aucun document confidentiel n'a été utilisé ; les rapports ne contiennent que des chiffres.

## Décision

**Surya 2 servi par `llama-server` CUDA, sans Docker, géré par le worker.** Surya 1 en processus (modèle GED_REACT) reste le plan B.

## Comparatif (12 pages « délavé »)

| | Surya 1 | **Surya 2 (llama.cpp)** | PaddleOCR-VL-1.6 |
|---|---|---|---|
| Mots retrouvés, arabe | 71,2 % | 70,3 % | 53,6 % |
| Mots retrouvés, français | 89,2 % | **90,5 %** | 81,5 % |
| Précision des mots (ensemble) | 80,4 % | **83,1 %** | 72 % |
| Tableaux détectés (référence : 11) | 5 | **11** | 11 |
| Images détectées | 7 | 16 | 10 |
| Fausse étiquette `Handwriting` | 6 | **0** | n/a |
| Secondes par page (1050 Ti) | 163 | 93 | 75 (49,9 en propre) |
| VRAM, pic | 3 966 Mo | **2 130 Mo** | 2 688 Mo |
| Infrastructure | Python seul | `llama-server` | environnement de 6,4 Go, moteur Paddle inutilisable sur Pascal |

## Constats

1. **L'arabe reste le point faible** : ~70 % des mots avec Surya, et c'est vrai aussi sur pages propres (73 % pour Surya 1).
   **Correction du 07/10/2026 : ce chiffre est un artefact de mesure.** La référence (couche texte des PDF) coupe les mots
   arabes (« رشي د ب ن ناص ر »), utilise les chiffres arabes-indiens et d'autres tirets. Avec la mesure par trigrammes de
   caractères normalisés (`rappel_car` de `evaluate.py`), la même page arabe passe de 51 % (mots) à 95,6 % : la lecture
   de l'arabe est bonne sur ces échantillons.
   PaddleOCR-VL est nettement pire (54 %, avec une boucle de génération sur une page) : abandonné.
2. **La dégradation « délavé » coûte peu en lecture du texte** (2 à 3 points de mots) **mais beaucoup en mise en page** pour
   Surya 1 (5 tableaux au lieu de 11, faux `Handwriting`). Surya 2 y résiste (11 tableaux, aucun `Handwriting`).
3. **Surya 2 est 1,75 fois plus rapide que Surya 1 et deux fois plus léger**, ce qui compte avec un GPU partagé.
4. **llama.cpp** : un build compilé pour une seule architecture CUDA plante sur l'autre ; le `llama-server` winget (Vulkan)
   s'arrête sans message à l'encodage d'image ; Surya ne sait pas arrêter son serveur sous Windows et garde une trace
   périmée. Le worker lance et arrête donc lui-même `llama-server` (par PID) et attache Surya par URL.
5. **Docker / vLLM abandonnés** : inutiles (llama.cpp suffit), la 1050 Ti n'est pas prise en charge, licence Docker Desktop
   incertaine pour une administration, démarrage lent et instable (ticket Surya #543).

## Intégration réelle (application complète, 07/10/2026)

`backend/tests/test_real_surya.py` (`OCR_GPU_TESTS=1`) : upload d'un PDF de 2 pages dégradées, worker réel, `llama-server` CUDA arch 61
démarré et arrêté par le worker, Surya 2, export DOCX. Résultat : **document « terminé », 2 pages reconnues, 27 blocs** (en-têtes,
titres, texte, 2 tableaux, 3 images, 3 tampons détectés par la couleur d'encre), **180 s au total** (démarrage du serveur compris),
DOCX valide, et **à la fin `llama-server` arrêté et VRAM libérée** (16 Mo utilisés). Les 3 tampons correspondent aux tampons
factices ajoutés par `degrade.py`.

## Limites de la mesure

12 pages, un seul passage, documents du Journal officiel (moins dégradés que de vieux scans), référence issue de la couche texte
d'un PDF (ordre et formes arabes pouvant différer), profil « très délavé » non mesuré avec le moteur final, PaddleOCR-VL testé
sans réglage anti-boucle, **dots.ocr non testé**. Les vitesses sont celles d'une carte Pascal en FP32 : à refaire sur la RTX 3060.

## Pistes pour améliorer l'arabe

Test de `dots.ocr` ; mesure du prétraitement (profil `tres_delave`) sur Surya 2 ; modèle Ollama de correction fort en arabe (garde-fous
déjà en place : chiffres, taux de modification, langue) ; relecture facilitée (blocs « à vérifier » mis en avant).

## Livrables de la phase

`tools/degrade.py`, `tools/evaluate.py`, `tools/extract_reference.py`, `tools/ocr_backends.py`, `tools/run_surya2.sh`,
`docs/phase0-paddleocr-vl.md`, rapports chiffrés dans `rapports/`.

**Validation de l'utilisateur** : la phase 1 a été engagée sur instruction explicite de poursuivre le développement ; les points
restant à valider par l'utilisateur sont dans `REPRISE.md`.
