# DowFlow

DowFlow est une application Windows de téléchargement et de lecture de médias. Elle propose une interface Flask pour les vidéos, la musique et les téléchargements organisés par source.

## Fonctionnalités

- Téléchargement de vidéos et d'audio avec `yt-dlp`.
- Mode Turbo avec `aria2c` et connexions parallèles.
- Qualité adaptative et reprise sur les téléchargements qui échouent.
- Sous-titres convertis au format VTT quand FFmpeg est disponible.
- Recherche musicale, métadonnées, pochettes et bibliothèque locale.
- Extraction de pages nécessitant un navigateur avec Playwright.
- Organisation automatique dans `downloads/` et nettoyage des fichiers temporaires.

## Prérequis

- Windows 10 ou version ultérieure.
- Python 3.10 ou version ultérieure dans le `PATH`.
- Une connexion Internet.
- Node.js LTS recommandé pour obtenir davantage de formats YouTube.

Le script de démarrage peut installer ou vérifier les dépendances externes suivantes : Chromium Playwright, `aria2c` et FFmpeg.

## Installation et lancement

Depuis le dossier du projet, lancez :

```powershell
.\start.bat
```

Le script crée `.venv`, installe les paquets de `requirements.txt`, vérifie les outils nécessaires et démarre l'application.

L'interface est disponible à l'adresse suivante :

```text
http://127.0.0.1:5001
```

Pour une installation manuelle :

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
python app.py
```

## Cookies et profils navigateur

Certains sites peuvent demander une session ou bloquer les requêtes automatisées. DowFlow peut utiliser un profil Chrome local ou un fichier `youtube_cookies.txt` au format Netscape.

Ces fichiers peuvent donner accès à vos comptes. Ils sont volontairement exclus du dépôt par `.gitignore` et ne doivent jamais être publiés.

## Tests et diagnostic

Lancez les tests unitaires avec :

```powershell
python -m unittest discover -v
```

Le centre de diagnostic interactif se lance avec :

```powershell
.\debug.bat
```

## Export et accès depuis d'autres appareils

Chaque espace possède un bouton **Exporter**. Il permet de choisir un nom et un mode de publication :

- **Lien HTTP / HTTPS** : ouvre l'espace courant et affiche l'adresse à partager.
- **Cloudflare Tunnel** : lancez `cloudflared tunnel --url http://127.0.0.1:5001` pour obtenir une URL HTTPS publique.
- **ngrok** : lancez `ngrok http 5001` pour obtenir une URL HTTPS publique.
- **Application web installable** : le manifeste PWA permet d'installer l'espace depuis un navigateur compatible, sur Windows, Android, iPhone, macOS ou Linux.

Pour autoriser les appareils du même réseau local à accéder au serveur :

```powershell
$env:DOWFLOW_HOST = "0.0.0.0"
python app.py
```

L'adresse à partager sera alors l'adresse IP locale de l'ordinateur, par exemple `http://192.168.1.20:5001`. Le serveur doit rester démarré. Pour un accès public, utilisez un tunnel authentifié et ne partagez jamais vos cookies ou vos identifiants.

## Organisation du projet

| Fichier ou dossier | Rôle |
| --- | --- |
| `app.py` | Serveur Flask et routes de l'interface |
| `playlist.py` | Téléchargements vidéo et audio |
| `music.py` | Recherche et gestion de la bibliothèque musicale |
| `music_dataset.py` | Accès au catalogue musical local |
| `browser_extractor.py` | Extraction avec navigateur |
| `url_expanders/` | Gestion des URLs et des redirections |
| `templates/` | Pages HTML de l'application |
| `static/` | Ressources statiques |
| `downloads/` | Médias téléchargés localement |
| `logs/` | Journaux d'exécution locaux |

## Configuration

Les paramètres principaux se trouvent dans `config.py`. Plusieurs options peuvent être contrôlées par des variables d'environnement, notamment :

- `DOWFLOW_BROWSER_VISIBLE` pour afficher le navigateur Playwright.
- `DOWFLOW_CONNECTIONS` pour définir le nombre de connexions parallèles.
- `DOWFLOW_PROXY_AUTO` pour activer la récupération de proxys publics.

Les données générées, les profils navigateur, les bases SQLite, les téléchargements et les journaux restent locaux et ne doivent pas être commités.
