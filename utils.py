import os
import shutil
from pathlib import Path
from typing import Any
from config import FFMPEG_FALLBACK_DIRS

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


__all__ = [
    "_resolve_ffmpeg_dir",
    "find_chrome_binary",
    "has_ffmpeg",
    "is_http_url",
    "yt_dlp_runtime_options",
]
