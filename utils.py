import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, build_opener, urlopen

from config import (
    FFMPEG_FALLBACK_DIRS,
    PROXIES,
    PROXY_AUTO,
    PROXY_POOL_TTL_SECONDS,
    PROXY_SOURCES,
    PROXY_VALIDATE_TIMEOUT,
    PROXY_VALIDATE_URL,
)

# Detection FFmpeg mise en cache : ces fonctions sont appelees a chaque rendu de page
# et a chaque telechargement (shutil.which fait un scan du PATH, couteux s'il est repete).
@lru_cache(maxsize=1)
def _resolve_ffmpeg_dir() -> str | None:
    """Retourne le chemin vers FFmpeg s'il est dans les dossiers de fallback, sinon None."""
    system_ffmpeg = shutil.which("ffmpeg")
    system_ffprobe = shutil.which("ffprobe")
    if system_ffmpeg and system_ffprobe:
        return None

    for path in FFMPEG_FALLBACK_DIRS:
        if (path / "ffmpeg.exe").exists() and (path / "ffprobe.exe").exists():
            return str(path)

    return None
@lru_cache(maxsize=1)
def has_ffmpeg() -> bool:
    """Vérifie si FFmpeg et FFprobe sont disponibles."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return True
    return _resolve_ffmpeg_dir() is not None

def is_http_url(value: str) -> bool:
    """Vérifie si une chaîne est une URL HTTP(S)."""
    return value.startswith("http://") or value.startswith("https://")

def yt_dlp_runtime_options(include_browser_cookies: bool = False) -> dict[str, Any]:
    """Configure le runtime JavaScript, le solveur EJS et les cookies du navigateur."""
    options: dict[str, Any] = {"remote_components": ["ejs:github"]}
    if include_browser_cookies:
        options["cookiesfrombrowser"] = ("chrome",)
    node_path = shutil.which("node")
    if node_path:
        options["js_runtimes"] = {"node": {"path": node_path}}
    return options

def find_chrome_binary() -> Path | None:
    """Retourne le chemin vers l'exécutable Chrome, ou None s'il n'est pas installé."""
    candidates: list[Path] = []
    if os.name == "nt":
        program_files = [os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                         os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")]
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        for base in program_files:
            if base:
                candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
        if local_appdata:
            candidates.append(Path(local_appdata) / "Google" / "Chrome" / "Application" / "chrome.exe")
    else:
        candidates.append(Path("/usr/bin/google-chrome"))
        candidates.append(Path("/usr/bin/chromium-browser"))
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))

    for path in candidates:
        if path.exists():
            return path
    return None


# ── Palier de connexions parallèles (8 → 128) ────────────────────────────────
# On exploite la puissance de la machine sans la saturer : les cœurs logiques
# plafonnent le palier, le débit réseau mesuré le limite au strict nécessaire
# (inutile d'ouvrir plus de connexions que la ligne ne peut en alimenter).
_CONNECTION_TIERS = (8, 16, 32, 64, 128)


def pick_connection_tier(cores: int, speed_mbps: float) -> int:
    """Choisit le palier de connexions (8/16/32/64/128) selon CPU + débit réseau."""
    if cores <= 2:
        cpu_tier = 8
    elif cores <= 4:
        cpu_tier = 16
    elif cores <= 8:
        cpu_tier = 32
    elif cores <= 16:
        cpu_tier = 64
    else:
        cpu_tier = 128

    if speed_mbps <= 0:
        # Débit inconnu : on ne prend aucun risque de saturation.
        return min(cpu_tier, 16)
    if speed_mbps < 10:
        net_tier = 8
    elif speed_mbps < 30:
        net_tier = 16
    elif speed_mbps < 80:
        net_tier = 32
    elif speed_mbps < 200:
        net_tier = 64
    else:
        net_tier = 128
    return min(cpu_tier, net_tier)


# ── Pool de proxys (gratuits, validés à la volée) ────────────────────────────
_proxy_lock = threading.RLock()
_proxy_pool: list[str] = []
_proxy_fetched_at = 0.0


def _fetch_free_proxies(limit: int = 60) -> list[str]:
    """Récupère une liste de proxys publics gratuits depuis les sources connues."""
    found: list[str] = []
    seen: set[str] = set()
    for source in PROXY_SOURCES:
        try:
            with urlopen(source, timeout=PROXY_VALIDATE_TIMEOUT) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
        except Exception:
            continue
        for line in text.splitlines():
            candidate = line.strip()
            if not candidate or ":" not in candidate or candidate in seen:
                continue
            seen.add(candidate)
            found.append(f"http://{candidate}")
            if len(found) >= limit:
                return found
    return found


def _validate_proxy(proxy: str) -> bool:
    """Vérifie qu'un proxy répond réellement (test HTTP court et léger)."""
    try:
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
        with opener.open(PROXY_VALIDATE_URL, timeout=PROXY_VALIDATE_TIMEOUT) as resp:
            return resp.status < 400
    except Exception:
        return False


def _refresh_proxy_pool() -> list[str]:
    """Reconstruit le pool (fetch + validation en parallèle) puis le met en cache."""
    global _proxy_pool, _proxy_fetched_at
    candidates = _fetch_free_proxies()
    if not candidates:
        _proxy_pool, _proxy_fetched_at = [], time.time()
        return []
    workers = min(24, max(4, len(candidates)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_validate_proxy, candidates))
    _proxy_pool = [proxy for proxy, ok in zip(candidates, results) if ok]
    _proxy_fetched_at = time.time()
    return _proxy_pool


def get_active_proxies() -> list[str]:
    """Proxys utilisables : liste manuelle (config.PROXIES) puis pool gratuit validé.

    Retourne une liste vide si aucun proxy n'est configuré/activé : le trafic
    reste alors direct, sans aucune régression.
    """
    if PROXIES:
        return list(PROXIES)
    if not PROXY_AUTO:
        return []
    with _proxy_lock:
        fresh = (time.time() - _proxy_fetched_at) < PROXY_POOL_TTL_SECONDS
        if _proxy_pool and fresh:
            return list(_proxy_pool)
        return list(_refresh_proxy_pool())


def pick_proxy() -> str | None:
    """Retourne le premier proxy actif, ou None si le mode direct est utilisé."""
    proxies = get_active_proxies()
    return proxies[0] if proxies else None


__all__ = [
    "_resolve_ffmpeg_dir",
    "find_chrome_binary",
    "get_active_proxies",
    "has_ffmpeg",
    "is_http_url",
    "pick_connection_tier",
    "pick_proxy",
    "yt_dlp_runtime_options",
]
