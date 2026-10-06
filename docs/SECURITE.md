# Sécurité et confidentialité (PLAN.md §4 bis)

## Mesures appliquées par l'application

- **Isolation stricte** : toute requête sur un document filtre sur `user_id` ; un autre utilisateur obtient 404 (jamais 403).
  L'administrateur voit les statistiques et les métadonnées, **jamais le contenu** (testé).
- **Fichiers** : stockés sous `data\{uploads,pages,crops,exports}\<utilisateur>\<document>\<nom aléatoire>`, hors de la
  racine web, servis uniquement par des routes authentifiées qui vérifient le propriétaire. Le nom d'origine n'est jamais
  utilisé sur le disque. Toute résolution de chemin refuse de sortir du dossier de données.
- **Suppression réelle** : fichiers, pages, découpes, exports et lignes en base ; purge automatique après `retention_days` ;
  images prétraitées temporaires effacées à la fin de chaque traitement.
- **Journaux** : aucun texte OCR, prompt ou réponse du modèle, ni nom de fichier d'origine ; les erreurs sont réduites à leur type.
- **Authentification** : mots de passe hachés (argon2), changement imposé à la première connexion, verrouillage après
  5 échecs, message d'erreur identique que l'identifiant existe ou non, jeton d'accès court (15 min, en mémoire du
  navigateur) et jeton de rafraîchissement en cookie `HttpOnly` + `SameSite=Strict` + `Secure` ; expiration après
  inactivité (fenêtre glissante) ; déconnexion et désactivation invalident immédiatement les sessions.
- **Journal d'audit** : connexion, dépôt, téléchargement, suppression, changements d'administration (identifiants seulement).
- **Contenu affiché** : le HTML des blocs est assaini côté serveur (liste blanche de balises) avant affichage et avant export ;
  en-têtes de sécurité (CSP, `X-Frame-Options`, `nosniff`, `no-store`) ; pas de documentation d'API exposée.
- **Hors ligne** : `HF_HUB_OFFLINE=1`, aucun téléchargement à l'exécution ; un test échoue en cas d'appel réseau sortant ;
  seuls les modèles Ollama **locaux** sont acceptés (les modèles `:cloud` sont refusés par l'application).
- **Services locaux** : `llama-server`, Ollama et MariaDB n'écoutent qu'en `127.0.0.1`.

## À faire côté poste (hors application)

1. **BitLocker** sur le disque de données (chiffrement au repos). Un chiffrement applicatif des fichiers reste possible
   en option (clé hors base) : non activé par défaut.
2. **Pare-feu** : `firewall.ps1` n'autorise l'entrée que depuis le sous-réseau local sur le port HTTPS et bloque la
   sortie de Python, llama-server et Ollama. Désactiver les mises à jour automatiques d'Ollama.
3. **Certificat** : `make_cert.py` produit un certificat interne auto-signé ; le distribuer aux postes clients, ou le
   remplacer par un certificat de l'autorité interne.
4. **Sauvegardes** : `backup.ps1` chiffre en AES-256 (7-Zip) ; le mot de passe est conservé hors du poste.
5. Droits NTFS : `.env` et `server.key` ne sont lisibles que par SYSTEM et les administrateurs (appliqué par `install.ps1`).

## Pendant le développement

Aucun document réel ne doit atteindre l'outil de développement assisté : les scans réels restent dans un dossier externe,
exclu du dépôt. `tools/evaluate.py` n'écrit qu'un rapport chiffré (jamais de texte, d'image ni de nom de fichier).

## Limites connues

- Les poids de Surya sont gratuits sous 5 M$ de revenu **ou de financement** : confirmation écrite de Datalab à obtenir.
- Le bundle contient des modèles tiers : vérifier leurs licences avant redistribution.
