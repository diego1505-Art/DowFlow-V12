@echo off
chcp 65001 >nul 2>&1
title DowFlow - Media Downloader
cd /d "%~dp0"

REM ── Venv ────────────────────────────────────────────────────────────────
REM Chemin absolu recalcule a chaque lancement : le venv reste valide meme si
REM le dossier du projet est renomme/deplace (sinon activation cassee -> Python
REM systeme + yt-dlp trop ancien -> erreurs HTTP 403 sur YouTube).
set "VENV_DIR=%~dp0.venv"
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creation du venv...
    python -m venv "%VENV_DIR%"
    "%VENV_DIR%\Scripts\python.exe" -m pip install -r requirements.txt
)
set "VIRTUAL_ENV=%VENV_DIR%"
set "PATH=%VENV_DIR%\Scripts;%PATH%"

REM ── Ecran de demarrage anime ────────────────────────────────────────────
python -c "import rich" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installation de Rich pour l'ecran de demarrage...
    python -m pip install "rich>=13.7.1,<16"
)
python -c "import rich" >nul 2>&1
if not errorlevel 1 (
    python startup.py
) else (
    echo [INFO] Animation indisponible; demarrage de DowFlow.
)

REM ── Playwright / Chromium : verifier a chaque lancement ──────────────────
python -c "from playwright.sync_api import sync_playwright as s; p=s().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()" 1>nul 2>nul
if errorlevel 1 (
    echo [INFO] Playwright Chromium manquant ou corrompu.
    echo [INFO] Tentative d'installation rapide...
    echo [INFO] Si cela prend trop de temps, vous pouvez fermer cette fenêtre.
    python -m playwright install chromium
    if errorlevel 1 (
        echo [ATTENTION] L'installation de Playwright a echoue. 
        echo [ATTENTION] Les telechargements Franime pourraient ne pas fonctionner.
    )
)

REM ── aria2c : verifier a chaque lancement, pas seulement a la creation ──
aria2c --version >nul 2>&1
if errorlevel 1 (
    echo Installation de aria2c...
    winget install aria2.aria2 --silent --accept-package-agreements --accept-source-agreements
    REM Ajouter le chemin winget au PATH pour cette session
    for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\aria2.aria2*") do (
        for /d %%S in ("%%D\aria2*") do set "PATH=%%S;%PATH%"
    )
) else (
    echo aria2c OK
)

REM ── FFmpeg ───────────────────────────────────────────────────────────────
set "FFMPEG_BIN=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin"
if exist "%FFMPEG_BIN%\ffmpeg.exe" set "PATH=%FFMPEG_BIN%;%PATH%"

REM ── Runtime JavaScript pour yt-dlp / YouTube ─────────────────────────────
where node >nul 2>&1
if errorlevel 1 (
    echo [ATTENTION] Node.js absent : l'extraction YouTube peut proposer moins de formats.
    echo [INFO] Installe Node.js LTS puis relance DowFlow.
) else (
    node --version
)

echo DowFlow est disponible sur http://127.0.0.1:5001
echo Ouvrez ou actualisez votre onglet existant. Aucun nouveau Chrome ne sera lance.
python app.py
pause
