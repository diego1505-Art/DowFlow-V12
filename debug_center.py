#!/usr/bin/env python3
"""
DowFlow Debug Center — menu interactif lisible (ASCII).
Lance via debug.bat ou : python debug_center.py
"""
from __future__ import annotations

import importlib
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SESSION_LOG = LOG_DIR / f"debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# Forcer UTF-8 console Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Activer VT100 couleurs si possible (Windows 10+)
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


class C:
    """Couleurs ANSI (fallback silencieux si non supporté)."""
    USE = sys.stdout.isatty()
    RESET = "\033[0m" if USE else ""
    BOLD = "\033[1m" if USE else ""
    DIM = "\033[2m" if USE else ""
    RED = "\033[91m" if USE else ""
    GRN = "\033[92m" if USE else ""
    YEL = "\033[93m" if USE else ""
    BLU = "\033[94m" if USE else ""
    CYN = "\033[96m" if USE else ""


def log_line(msg: str = "") -> None:
    plain = (
        msg.replace(C.RESET, "")
        .replace(C.BOLD, "")
        .replace(C.DIM, "")
        .replace(C.RED, "")
        .replace(C.GRN, "")
        .replace(C.YEL, "")
        .replace(C.BLU, "")
        .replace(C.CYN, "")
    )
    print(msg)
    try:
        with SESSION_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {plain}\n")
    except OSError:
        pass


def ok(msg: str) -> None:
    log_line(f"  {C.GRN}[ OK ]{C.RESET} {msg}")


def warn(msg: str) -> None:
    log_line(f"  {C.YEL}[WARN]{C.RESET} {msg}")


def fail(msg: str) -> None:
    log_line(f"  {C.RED}[FAIL]{C.RESET} {msg}")


def info(msg: str) -> None:
    log_line(f"  {C.CYN}[INFO]{C.RESET} {msg}")


def section(title: str) -> None:
    log_line()
    log_line(f"  {C.BOLD}-- {title} --{C.RESET}")
    log_line(f"  {'-' * (len(title) + 6)}")


def pause() -> None:
    input(f"\n  {C.DIM}Entree pour revenir au menu...{C.RESET}")


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def ensure_path_tools() -> None:
    """Ajoute FFmpeg / aria2 Winget au PATH de la session."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    ffmpeg = (
        local
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / "ffmpeg-8.1.1-full_build"
        / "bin"
    )
    if ffmpeg.is_dir() and str(ffmpeg) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = str(ffmpeg) + os.pathsep + os.environ.get("PATH", "")

    packages = local / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        for d in packages.glob("aria2.aria2*"):
            for sub in d.glob("aria2*"):
                exe = sub / "aria2c.exe"
                if exe.is_file():
                    os.environ["PATH"] = str(sub) + os.pathsep + os.environ.get("PATH", "")
                    return


# ── Checks ───────────────────────────────────────────────────────────────────

def check_python() -> None:
    section("Python + environnement")
    ok(f"Python {sys.version.split()[0]}  ({sys.executable})")
    venv = BASE / ".venv" / "Scripts" / "python.exe"
    if venv.is_file():
        ok(f"Virtualenv present : {venv}")
    else:
        warn("Virtualenv .venv absent — lance start.bat pour le creer")
    
    for folder in ("downloads", "downloads/music"):
        path = BASE / folder
        if path.is_dir():
            count = sum(1 for _ in path.rglob('*') if _.is_file())
            ok(f"Dossier {folder}/ OK ({count} fichiers)")
        else:
            path.mkdir(parents=True, exist_ok=True)
            ok(f"Dossier {folder}/ cree")
    info(f"Session log : {SESSION_LOG}")


def check_deps() -> None:
    section("Dependances Python")
    for mod in ("flask", "yt_dlp", "playwright", "requests"):
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "?")
            ok(f"{mod} v{ver}")
        except Exception as e:
            fail(f"{mod} absent — {e}")

    section("Imports codebase")
    modules = (
        "app",
        "playlist",
        "music",
        "music_dataset",
        "browser_extractor",
        "franime_extractor",
        "config",
        "utils",
        "logger",
        "database",
        "url_expanders",
        "jobs",
    )
    for name in modules:
        try:
            importlib.import_module(name)
            ok(name)
        except Exception as e:
            fail(f"{name} — {e}")


def check_tools() -> None:
    section("Outils externes")
    ensure_path_tools()
    for tool in ("ffmpeg", "ffprobe", "aria2c"):
        path = shutil.which(tool)
        if path:
            ok(f"{tool} → {path}")
        elif tool == "aria2c":
            warn(f"{tool} absent (optionnel, accelere les DL)")
        else:
            fail(f"{tool} introuvable")

    chrome_candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    chrome = next((p for p in chrome_candidates if p.is_file()), None)
    if chrome:
        ok(f"Chrome → {chrome}")
    else:
        warn("Chrome non detecte (utile pour profil cookies Playwright)")


def check_playwright() -> None:
    section("Playwright + navigateur")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        fail(f"Module playwright absent — {e}")
        return

    ok("Module playwright importe")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("about:blank")
            browser.close()
        ok("Chromium lance + page blanche OK")
    except Exception as e:
        warn(f"Chromium KO — tentative d'install : {e}")
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True)
                b.close()
            ok("Chromium installe et fonctionnel")
        except Exception as e2:
            fail(f"Chromium toujours KO — {e2}")

    profile = BASE / "browser_profile"
    if not profile.is_dir():
        profile.mkdir(parents=True, exist_ok=True)
        ok("browser_profile/ cree")
    else:
        ok("browser_profile/ present")


def test_ytdlp() -> None:
    section("Test yt-dlp (metadata YouTube, sans telecharger)")
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    info(f"URL : {url}")
    try:
        import yt_dlp

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
        }
        from utils import yt_dlp_runtime_options
        opts.update(yt_dlp_runtime_options())
        with yt_dlp.YoutubeDL(opts) as ydl:
            meta = ydl.extract_info(url, download=False)
        ok(f"Titre   : {meta.get('title')}")
        ok(f"Duree   : {meta.get('duration')} s")
        ok(f"Formats : {len(meta.get('formats') or [])}")
    except Exception as e:
        fail(f"yt-dlp erreur — {e}")


def test_network() -> None:
    section("Reseau / DNS")
    for host in ("8.8.8.8", "1.1.1.1"):
        try:
            sock = socket.create_connection((host, 53), timeout=2)
            sock.close()
            ok(f"Connectivite {host}:53")
        except OSError:
            fail(f"Pas de reponse {host}")

    section("Sites cibles")
    targets = (
        "https://www.youtube.com",
        "https://franime.fr",
        "https://api.deezer.com",
        "https://video.sibnet.ru",
    )
    for url in targets:
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "DowFlow-Debug/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                ok(f"{url} → HTTP {resp.status}")
        except Exception as e:
            fail(f"{url} → {e}")


def check_codebase() -> None:
    section("Fichiers essentiels")
    files = [
        "app.py",
        "playlist.py",
        "music.py",
        "music_dataset.py",
        "config.py",
        "utils.py",
        "logger.py",
        "database.py",
        "jobs.py",
        "browser_extractor.py",
        "franime_extractor.py",
        "templates/menu.html",
        "templates/dowload.html",
        "templates/music.html",
        "templates/coming_soon.html",
        "requirements.txt",
        "url_expanders/__init__.py",
        "url_expanders/base.py",
        "url_expanders/franime.py",
        "url_expanders/youtube.py",
        "url_expanders/generic.py",
    ]
    for rel in files:
        path = BASE / rel
        if path.is_file():
            ok(f"{rel} ({path.stat().st_size} octets)")
        else:
            fail(f"{rel} MANQUANT")

    section("Healthcheck Flask APIs")
    try:
        import threading
        import requests
        from app import app

        def run_server():
            app.run(host="127.0.0.1", port=5099, debug=False, use_reloader=False)

        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        time.sleep(2)
        
        # Check /config
        r = requests.get("http://127.0.0.1:5099/config", timeout=5)
        data = r.json()
        ok(f"/config OK (has_ffmpeg={data.get('has_ffmpeg')})")
        
        # Check /api/music/popular
        r_mus = requests.get("http://127.0.0.1:5099/api/music/popular?limit=1", timeout=5)
        mus_data = r_mus.json()
        if mus_data.get("success"):
            ok(f"/api/music/popular OK ({len(mus_data.get('tracks', []))} titres)")
        else:
            warn("/api/music/popular a echoue")

    except Exception as e:
        fail(f"Healthcheck Flask — {e}")


def check_database() -> None:
    section("Base SQLite (historique)")
    try:
        from database import (
            DB_PATH,
            download_count_by_status,
            download_list,
            db_is_ready,
        )
        from database import _conn  # noqa: PLC2701

        if not db_is_ready():
            warn(f"DB absente ou vide : {DB_PATH}")
            return
        ok(f"DB : {DB_PATH} ({DB_PATH.stat().st_size} octets)")
        counts = download_count_by_status()
        info(f"Stats status : {counts or '{}'}")
        rows = download_list(limit=8)
        if not rows:
            info("Aucun telechargement enregistre")
            return
        log_line(f"  {C.DIM}Derniers telechargements :{C.RESET}")
        for row in rows:
            status = row.get("status")
            url = (row.get("url") or "")[:70]
            color = C.GRN if status == "success" else (C.YEL if status == "skipped" else C.RED)
            log_line(f"    {color}{status:8}{C.RESET} #{row.get('id')} {url}")

        with _conn() as c:
            strategies = c.execute(
                "SELECT domain, preferred_extractor, success_count, fail_count "
                "FROM site_strategy ORDER BY success_count DESC LIMIT 10"
            ).fetchall()
        if strategies:
            section("Strategies par domaine")
            for s in strategies:
                ok(
                    f"{s['domain']}: extractor={s['preferred_extractor']} "
                    f"ok={s['success_count']} fail={s['fail_count']}"
                )
    except Exception as e:
        fail(f"Database — {e}")


def check_config_dump() -> None:
    section("Config DowFlow")
    try:
        import config

        info(f"DOWNLOAD_FOLDER = {config.DOWNLOAD_FOLDER}")
        info(f"BROWSER_HEADLESS = {config.BROWSER_HEADLESS}")
        info(f"VIDEO formats   = {list(config.VIDEO_OUTPUT_FORMATS)}")
        info(f"AUDIO formats   = {list(config.AUDIO_OUTPUT_FORMATS)}")
        info(f"Qualities       = {list(config.VIDEO_QUALITIES)}")
        info(f"Langues         = {len(config.LANGUAGES)} codes")
        info(f"DOWFLOW_DEBUG   = {os.environ.get('DOWFLOW_DEBUG', '(non defini)')}")
    except Exception as e:
        fail(str(e))


def clean_partials() -> None:
    section("Nettoyage fichiers partiels (.part / .aria2 / .ytdl)")
    downloads = BASE / "downloads"
    if not downloads.is_dir():
        warn("Pas de dossier downloads/")
        return
    patterns = ("*.part", "*.aria2", "*.ytdl")
    removed = 0
    freed = 0
    for pat in patterns:
        for path in downloads.rglob(pat):
            try:
                size = path.stat().st_size
                path.unlink()
                removed += 1
                freed += size
                info(f"Supprime : {path.relative_to(BASE)}")
            except OSError as e:
                fail(f"{path.name} — {e}")
    if removed:
        ok(f"{removed} fichier(s) supprime(s), {freed / 1024 / 1024:.1f} Mo liberes")
    else:
        ok("Aucun fichier partiel trouve")


def open_folders() -> None:
    section("Ouvrir dossiers")
    choices = {
        "1": BASE / "logs",
        "2": BASE / "downloads",
        "3": BASE / "downloads/music",
        "4": BASE,
    }
    print("  Option 1 - logs/")
    print("  Option 2 - downloads/")
    print("  Option 3 - downloads/music/")
    print("  Option 4 - racine projet")
    print("  Option 0 - annuler")
    choice = input(f"\n  {C.BOLD}Choix : {C.RESET}").strip()
    path = choices.get(choice)
    if not path:
        return
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
    ok(f"Ouvert : {path}")


def start_debug_server() -> None:
    section("Demarrage DowFlow en mode DEBUG")
    ensure_path_tools()
    env = os.environ.copy()
    env["DOWFLOW_DEBUG"] = "1"
    env["FLASK_DEBUG"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    info("DOWFLOW_DEBUG=1  FLASK_DEBUG=1")
    info("URL : http://127.0.0.1:5001")
    info("Ctrl+C pour arreter")
    if os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", "", "http://127.0.0.1:5001"])
    try:
        subprocess.run([sys.executable, str(BASE / "app.py")], cwd=str(BASE), env=env, check=False)
    except KeyboardInterrupt:
        warn("Serveur arrete")


def full_diagnostic() -> None:
    log_line(f"\n  {C.BOLD}DIAGNOSTIC COMPLET{C.RESET}")
    check_python()
    check_deps()
    check_tools()
    check_playwright()
    check_codebase()
    check_database()
    check_config_dump()
    test_network()
    log_line()
    ok(f"Rapport sauvegarde : {SESSION_LOG}")


# ── Menu ─────────────────────────────────────────────────────────────────────

MENU = [
    ("1", "Diagnostic COMPLET (tous les checks)", full_diagnostic),
    ("2", "Verifier Python + dossiers downloads", check_python),
    ("3", "Verifier dependances + imports codebase", check_deps),
    ("4", "Verifier FFmpeg / FFprobe / aria2c / Chrome", check_tools),
    ("5", "Verifier Playwright + navigateur", check_playwright),
    ("6", "Test yt-dlp YouTube (metadata)", test_ytdlp),
    ("7", "Test reseau + sites cibles (YouTube, Deezer...)", test_network),
    ("8", "Fichiers projet + healthcheck APIs", check_codebase),
    ("9", "Historique SQLite + strategies sites", check_database),
    ("10", "Dump config (formats, qualites, env)", check_config_dump),
    ("11", "Nettoyer fichiers partiels (.part / .aria2)", clean_partials),
    ("12", "Ouvrir logs / downloads / music / projet", open_folders),
    ("13", "Demarrer DowFlow en mode DEBUG", start_debug_server),
]


def show_menu() -> None:
    clear()
    print()
    print(f"  {C.BOLD}{'=' * 58}{C.RESET}")
    print(f"  {C.BOLD}       DOWFLOW DEBUG CENTER{C.RESET}")
    print(f"  {C.DIM}       Diagnostic toolkit — ASCII / lisible{C.RESET}")
    print(f"  {C.BOLD}{'=' * 58}{C.RESET}")
    print(f"  {C.DIM}Log session : {SESSION_LOG.name}{C.RESET}")
    print(f"  {C.DIM}Dossier     : {BASE}{C.RESET}")
    print()
    print(f"  {C.BOLD}Options :{C.RESET}")
    print(f"  {'-' * 58}")
    for key, label, _ in MENU:
        print(f"  {C.YEL}Option {key:>2}{C.RESET}  -  {label}")
    print(f"  {C.YEL}Option  0{C.RESET}  -  Quitter")
    print(f"  {'-' * 58}")
    print()


def main() -> int:
    os.chdir(BASE)
    ensure_path_tools()
    # Preferer le venv si on est lance hors venv
    venv_py = BASE / ".venv" / "Scripts" / "python.exe"
    if venv_py.is_file() and Path(sys.executable).resolve() != venv_py.resolve():
        # Relancer dans le venv pour les checks deps
        if "--no-reexec" not in sys.argv:
            return subprocess.call([str(venv_py), str(BASE / "debug_center.py"), "--no-reexec"])

    actions = {key: fn for key, _, fn in MENU}
    while True:
        show_menu()
        choice = input(f"  {C.BOLD}Choisis une option : {C.RESET}").strip()
        if choice in {"0", "q", "Q"}:
            clear()
            print(f"\n  Rapport : {SESSION_LOG}\n")
            return 0
        action = actions.get(choice)
        if not action:
            warn("Option inconnue")
            time.sleep(0.8)
            continue
        clear()
        print()
        try:
            action()
        except KeyboardInterrupt:
            warn("Interrompu")
        except Exception as e:
            fail(f"Erreur : {e}")
            import traceback
            traceback.print_exc()
        if choice != "13":
            pause()


if __name__ == "__main__":
    raise SystemExit(main())
