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
- [ ] Juger la qualité de l'**arabe** (≈ 70 % des mots retrouvés sur nos échantillons) : c'est le point faible principal ;
      voir `docs/phase0-paddleocr-vl.md` pour l'alternative testée.
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

