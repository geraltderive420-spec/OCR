# Reprise : ce qui reste à faire par vous

Le développement des phases 1 à 9 est terminé et testé en développement (voir `PLAN.md`). Les points ci-dessous
**ne peuvent pas être faits sans vous** : ils dépendent de votre matériel, de vos documents ou de décisions humaines.

## À valider ou à décider

- [ ] **Licence des poids Surya** : confirmation écrite de Datalab (seuil de 5 M$ de revenu *ou de financement* ; le budget
      d'une administration pourrait compter comme financement). Bloquant avant la mise en production.
- [ ] Les autres applications du GPU sont-elles toutes de l'organisation, sur le même Ollama ? (décide d'un éventuel
      verrou GPU commun ; sinon le contrôle de la VRAM libre suffit.)
- [ ] Taille maximale habituelle d'un PDF (pages) : réglable dans Administration > Paramètres (300 pages par défaut).
- [ ] Modèle Ollama de correction par défaut : `gemma4:e2b` (réglable) ; à comparer sur de vrais textes arabes.

## À mesurer sur la RTX 3060 (production)

- [ ] Compiler (ou copier) un `llama-server` **arch 86** et renseigner `OCR_LLAMA_CPP_DIR`.
- [ ] Lancer `python -m app.cli check` puis traiter quelques pages : vitesse réelle (la 1050 Ti donne ~93 s/page, la 3060
      devrait être bien plus rapide), VRAM, démarrage de `llama-server`, arrêt après inactivité, cohabitation avec vos autres applications.
- [ ] Tester l'encodeur d'image : sur la 1050 Ti il reste sur le processeur (« CLIP using CPU backend ») ; vérifier s'il passe sur le GPU en 3060.

## À faire avec vos vrais scans (jamais dans le dépôt)

- [ ] `python tools/evaluate.py <dossier de scans> --backend surya2 --llama-cpp-dir <Release> --sortie rapport.json`
      puis me transmettre **seulement** le rapport chiffré (jamais de texte ni d'image).
- [ ] Juger la qualité de l'**arabe** sur vos scans avec `rappel_car` / `precision_car` (les « ≈ 70 % des mots » mesurés
      en phase 0 étaient un artefact de la référence : ≈ 95 % des caractères en réalité sur nos échantillons).
- [ ] Repérer les erreurs de mise en page sur vos scans : `tools/annoter.py` dessine les blocs détectés (tout reste sur le poste).
- [ ] Profil `tres_delave` et prétraitement : vérifier que les tableaux sont retrouvés sur vos pires scans.

## Installation de production

- [ ] Suivre `docs/INSTALLATION.md` ; **tester l'installation câble réseau débranché** (critère d'acceptation de la phase 9).
- [ ] BitLocker sur le disque de données ; distribuer `server.crt` aux postes ; planifier `backup.ps1`.
- [ ] Les scripts PowerShell (`deploy/windows`) sont vérifiés syntaxiquement mais n'ont pas pu être exécutés sur une
      machine de production : prévoir une première installation « à blanc » et corriger ce qui s'impose.

## Limites connues

- Dernière étape de qualité à valider sur documents réels : mise en colonnes (une colonne par page en version 1),
  tampons très proches du texte, tableaux sans bordures.
- Surya étiquette parfois à tort des blocs imprimés « Handwriting » sur scans dégradés ; Surya 2 le fait beaucoup moins.

## Nettoyage possible

- `D:\ocr_work\` (≈ 10 Go) : environnement et poids de PaddleOCR-VL, abandonné (`venv\paddlevl`, `hf`, `paddlex`, caches pip / npm).
  À supprimer si vous ne voulez pas relancer ce test. Le cache npm de l'interface est dans `D:\ocr_work\npm`.
- `backend/data/` (documents de démonstration créés pendant le développement) et la base `ocr_dev` / `ocr_test` de MariaDB
  (utilisateur `ocr_app`, mot de passe dans `backend/.env`, ignoré par git) : à supprimer avant la mise en production.
- Le compte `admin` de démonstration créé dans `ocr_dev` (mot de passe de test) : à supprimer avec la base de développement.


## Améliorations de qualité du 07/10/2026 (mesurées)

Mesures sur 3 pages « délavé » (2 arabes, 1 française), Surya 2 sur la 1050 Ti :

| | Page arabe difficile : mots retrouvés / précision | Page française : précision |
|---|---|---|
| Avant (image prétraitée, profil auto = `tres_delave`) | 65,4 % / 75,7 % | 98,3 % |
| Image d'origine redressée | 72,8 % / 85,5 % | 100 % |
| + budget de tokens par bloc élargi | **77,8 % / 85,1 %** | 100 % |

- **Le prétraitement dégradait l'OCR** : la binarisation et le CLAHE effacent les points et les traits fins de l'arabe.
  Le profil « auto » ne fait plus que redresser la page ; les profils explicites restent disponibles.
- **Les blocs arabes étaient coupés** : Surya limite chaque bloc à l'estimation de la mise en page + 100 tokens, sans le signaler.
- **Mise en page Word** : les pages en deux colonnes deviennent des sections Word à deux colonnes (de droite à gauche pour l'arabe).
  La taille, le gras et l'alignement (justifié, centré) sont mesurés sur l'image pour chaque bloc. Les polices sont réglables
  dans Administration > Paramètres (défaut : Times New Roman et Simplified Arabic).
- Migration de base `0002` (colonne `blocks.style`) : appliquée par `update.ps1`. Les documents déjà traités gardent l'ancien rendu
  tant qu'ils ne sont pas retraités.
- [ ] Sur la RTX 3060 : essayer `OCR_LLAMA_PARALLEL=2` puis `4` (blocs d'une page traités en parallèle). Sur la 1050 Ti le gain
      n'est que de 3 % (GPU saturé). Chaque slot ajoute un contexte de 16k tokens en VRAM : ajuster `gpu_min_free_mb`.
- Essais sans effet, donc non retenus : relever le plafond d'image de llama.cpp (`--image-max-tokens 8192`) et calculer
  la mise en page sur une image réduite de moitié.

## Détection de la mise en page, des images et du texte (07/10/2026, suite)

Diagnostic visuel sur les pages d'exemple (`tools/annoter.py`) puis mesures sur les 12 pages de référence :

- **La mesure était trompeuse pour l'arabe** : nouvelle mesure `rappel_car` / `precision_car` dans `evaluate.py`
  (trigrammes de caractères normalisés, insensibles aux mots coupés et aux chiffres arabes-indiens de la référence).
- **Les tampons et signatures perturbaient toute la détection** : un tampon posé sur du texte était classé « image »
  (texte jamais lu), un titre tamponné était mal lu (« JOURNAL OFFICE PAMPON »), et autour des tampons la mise en page
  se fragmentait (36 petits blocs au lieu d'un tableau). La page est désormais lue **sans l'encre des tampons**
  (`stamps.erase_stamps` : on garde le canal clair de l'encre colorée, le texte noir dessous reste intact). Les tampons
  restent repérés par leur couleur sur l'original et ajoutés au Word comme images.
- **Boîtes recalées sur les lignes détectées** (`ocr/layout_refine.py`, détecteur de lignes de Surya sur processeur,
  ~7 s par page) : les boîtes trop serrées ne rognent plus le début des lignes ; les lignes qu'aucune boîte ne couvre
  deviennent des blocs. Réglage : `OCR_LAYOUT_REFINE=false` pour désactiver, `OCR_LINE_DETECTOR_DEVICE=cuda` sur la 3060.
- **Blocs revenus vides relus ligne par ligne** : le modèle renvoie parfois une chaîne vide pour un gros bloc lisible
  (bandeau grisé) ; sans ce repli, tout le bloc disparaissait.
- **Images** : leur boîte est agrandie jusqu'aux traits qu'elle coupait (tampon ou logo rogné).
- **Relecture** : boîtes colorées par type ; chaque bloc peut changer de type (texte pris pour une image, titre…)
  ou être supprimé (bruit, doublon).
- **Robustesse** : si Surya échoue à démarrer, `llama-server` est arrêté (auparavant il restait orphelin, occupait
  le port et laissait tous les documents « en attente du GPU »).
- Les résultats d'une même page peuvent varier de quelques points d'une session à l'autre : ne comparer que des moyennes.

Mesure sur les 12 pages de référence (toutes portent des tampons et signatures ajoutés par `degrade.py`) :

| | Caractères retrouvés | Précision | Secondes / page (1050 Ti) |
|---|---|---|---|
| Avant cette passe | 90,0 % | 87,2 % | 103 |
| Après | 89,9 % | 85,1 % | 149 |

En moyenne, pas de gain chiffré sur ces pages (les couvertures varient dans les deux sens : +3 points sur l'une, −4 sur
une autre ; la précision baisse de 2 points sans cause identifiée) ; le gain est **structurel** et visible : tableaux non
fragmentés, texte sous les tampons lu, bandeaux non perdus. Le surcoût de temps vient surtout de la double lecture des
blocs touchés par un tampon. À juger sur vos scans : `OCR_STAMP_ERASE=false` et `OCR_LAYOUT_REFINE=false` (dans
`backend/.env`, worker redémarré) rétablissent le comportement précédent pour comparer avec `tools/annoter.py`.

## Mise en page des courriers (07/10/2026, sur un vrai courrier)

Corrigé à partir d'un courrier bilingue jugé « catastrophique » (en-tête FR / logo / AR, référence, date, objet, signature) :

- **Lecture** : ligne d'une seule rangée faite de morceaux séparés par de grands blancs coupée en blocs (« N° : … رقم »,
  « Sidi Bel Abbès, le … سيدي بلعباس ») ; blocs lus trop courts pour leurs lignes relus ligne par ligne (en-tête dont le
  modèle perdait 2 lignes sur 3) ; logo d'en-tête (court, en haut, encre colorée) traité comme une image.
- **Mesures** (`typography.py`) : taille d'après la hauteur ligne de base → capitales (indépendante des jambages et de
  l'interligne ; rapport propre au latin et à l'arabe), gras par rapport au corps du texte, soulignement, retrait de
  1re ligne / suspendu, interligne, largeur de chaque ligne.
- **Word** : blocs côte à côte → rangée de tableau sans bordures ; position des blocs étroits par retraits (signature à
  droite) ; espaces verticaux ; retours à la ligne des titres et signatures ; en-tête d'une seule page laissé à sa place.
- **Police** : choisie à l'envoi (Arial par défaut ; Times New Roman pour le Journal officiel). Elle n'est pas détectée
  automatiquement : l'essai (empattements) marchait sur le courrier mais échouait sur le Journal officiel.
- Limite : une ligne coupée par le bord du scan reste illisible.
