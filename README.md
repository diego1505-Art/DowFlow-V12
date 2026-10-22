# 🚀 DowFlow - Universal Media Experience

DowFlow est un téléchargeur et lecteur multimédia universel, ultra-rapide et intelligent, conçu pour Windows. Il transforme l'expérience de téléchargement en une solution "clés en main" capable de gérer plus de 1000 sites (YouTube, Instagram, TikTok, Twitter, Twitch, etc.) avec une gestion dynamique de la qualité, de la musique et des sessions.

---

## 🌟 Points Forts

- **📱 Menu Multi-Catégories** : Un hub central pour choisir votre type de média : Vidéo, Musique, Film/Série, Anime, ou Manga/Images.
- **🎵 Espace Musique Dédié** :
    - **Recherche YouTube intégrée** : Trouvez n'importe quel morceau sans quitter l'application.
    - **Pochettes HD (Deezer)** : Récupération automatique des pochettes d'album via l'API Deezer (repli sur YouTube).
    - **Bibliothèque Locale** : Gérez vos morceaux téléchargés, écoutez-les avec le lecteur intégré et profitez de l'**Auto-Play** (lecture continue).
    - **Gestion Intelligente** : Bouton de suppression directe et effet "Marquee" pour les titres longs.
- **⚡ Turbo Mode (x16)** : Utilise `aria2c` avec 16 connexions simultanées pour saturer votre bande passante.
- **📊 Qualité Adaptative Dynamique** : Surveillance du débit réel pendant 10 secondes et rétrogradation automatique si nécessaire pour garantir un téléchargement fluide.
- **🎬 Lecteur Intelligent & Sous-titres** :
    - **Passe Unique** : Vidéo, Audio et Sous-titres récupérés ensemble.
    - **Format VTT** : Conversion automatique pour une compatibilité web parfaite.
    - **Bouton CC** : Contrôle des sous-titres à la volée.
- **📂 Organisation Automatique** : Médias triés par domaine ou par type (ex: `downloads/music/`, `downloads/youtube/`).
- **🍪 Gestion de Session Adaptative** : Utilise vos cookies Chrome ou `youtube_cookies.txt` pour contourner les bot-checks.

---

## 🛠️ Installation Windows

La méthode la plus simple est d'utiliser le lanceur automatisé :

```powershell
.\start.bat
```

Ce script s'occupe de tout : environnement virtuel, dépendances, Playwright (Chromium), aria2c et FFmpeg.

**Interface accessible sur :** `http://127.0.0.1:5001`

---

## 🎵 Focus Musique

L'espace Musique (`/music`) permet de :
1. **Chercher** : Résultats instantanés depuis YouTube.
2. **Télécharger** : Choix du format (MP3 192/320, FLAC, WAV...) avec inclusion automatique des métadonnées et pochettes.
3. **Écouter** : Votre bibliothèque locale est scannée en temps réel.
4. **Dataset (Futur)** : Support prévu pour le dataset MusicBrainz/Spotify/Tidal/Deezer (~34 Go de métadonnées) pour une recherche offline ultra-complète.

---

## 🗂️ Architecture du Projet

- **[app.py](file:///c:/Users/diego/Documents/download-video-youtube-V9-main/app.py)** : Serveur Flask, routes API et gestion des jobs.
- **[playlist.py](file:///c:/Users/diego/Documents/download-video-youtube-V9-main/playlist.py)** : Cœur logique des téléchargements vidéo.
- **[music.py](file:///c:/Users/diego/Documents/download-video-youtube-V9-main/music.py)** : Service dédié à la recherche et à la gestion musicale.
- **[music_dataset.py](file:///c:/Users/diego/Documents/download-video-youtube-V9-main/music_dataset.py)** : Interface pour le catalogue musical local.
- **[templates/](file:///c:/Users/diego/Documents/download-video-youtube-V9-main/templates)** : 
    - `menu.html` : Accueil DowFlow.
    - `dowload.html` : Espace Vidéo universel.
    - `music.html` : Espace Musique complet.
- **[test_music.py](file:///c:/Users/diego/Documents/download-video-youtube-V9-main/test_music.py)** : Tests spécifiques au workflow musical.

---

## 🛡️ OPSEC & Fiabilité

- **Nettoyage Automatique** : Suppression des fichiers `.part`, `.aria2` et `.ytdl` après chaque job.
- **Signatures & Hashs** : Respect des signatures de fichiers pour éviter les détections antivirus inutiles.
- **Logs Success** : Suivi précis des téléchargements réussis dans la console et la base SQLite.

---

## 📝 Diagnostics

En cas de problème, utilisez le centre de diagnostic interactif :
```powershell
.\debug.bat
```
Ou lancez les tests unitaires :
```powershell
python -m unittest discover -v
```
