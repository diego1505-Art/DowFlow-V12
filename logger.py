"""
DowFlow — Logging structuré cross-module.

Usage dans n'importe quel fichier :

    from logger import get_logger
    log = get_logger(__name__)     # ou get_logger("franime"), get_logger("yt-dlp")
    log.info("Téléchargement démarré")
    log.warning("FFmpeg non détecté, fallback conteneurs limités")
    log.error("Échec extraction : %s", exc)
    log.success("OK ! Fichier %s", path)           # niveau custom SUCCESS

Caractéristiques :
  • 2 sorties simultanées : console COULEUR + fichier logs/dowflow_YYYYMMDD.log
  • Niveau fichier = DEBUG (tout), niveau console = INFO (lisible)
  • Rotation automatique par jour, rétention 30 jours
  • Thread-safe, idempotent (appeler get_logger() 10x = même logger)
  • Niveau custom SUCCESS = vert console, tag [%tag[%]
  • Format machine : timestamp | NIVEAU | logger | message  → parseable par outils
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path

from config import BASE_DIR

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

SUCCESS_LEVELNO = 25  # entre INFO (20) et WARNING (30)
_LOGGERS: dict[str, logging.Logger] = {}
_LOCK = threading.Lock()
_CONFIGURED = False


# ── Ajout niveau custom SUCCESS ──────────────────────────────────────────────

def _success(self, msg, *args, **kwargs):
    if self.isEnabledFor(SUCCESS_LEVELNO):
        self._log(SUCCESS_LEVELNO, msg, args, **kwargs)


logging.addLevelName(SUCCESS_LEVELNO, "SUCCESS")
logging.Logger.success = _success  # type: ignore[attr-defined]


# ── Formatteur ──────────────────────────────────────────────────────────────

class _ColorFormatter(logging.Formatter):
    """Formateur console ANSI + tag [module]."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    COLORS = {
        logging.DEBUG: "\033[36m",       # cyan
        logging.INFO: "\033[37m",        # blanc
        SUCCESS_LEVELNO: "\033[92m",       # vert clair
        logging.WARNING: "\033[93m",        # jaune
        logging.ERROR: "\033[91m",          # rouge
        logging.CRITICAL: "\033[95m",        # magenta
    }

    def __init__(self, use_color: bool = True):
        super().__init__(
            fmt="%(asctime)s | %(levelname)-7s | %(name)-16s | %(message)s",
            datefmt="%H:%M:%S",
        )
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        if not self.use_color:
            return line
        color = self.COLORS.get(record.levelno, self.RESET)
        # Colorie la portion NIVEAU
        level_name = record.levelname
        start = line.find(level_name)
        if start >= 0:
            end = start + len(level_name)
            return (
                line[:start]
                + self.BOLD + color + level_name + self.RESET
                + line[end:]
            )
        return color + line + self.RESET


class _FileFormatter(logging.Formatter):
    """Format fichier : timestamp ISO, machine-readable, SANS couleurs."""

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s | %(levelname)-7s | %(name)-16s | %(process)d | %(thread)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


# ── Configuration globale ──────────────────────────────────────────────────────

def _configure() -> None:
    """Initialise la racine + handlers console + fichier rotatif."""
    global _CONFIGURED
    with _LOCK:
        if _CONFIGURED:
            return
        # ── Force UTF-8 sur stdout/stderr Windows (évite cp1252 charmap errors) ─
        try:
            import sys as _sys
            if hasattr(_sys.stdout, "reconfigure"):
                _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            if hasattr(_sys.stderr, "reconfigure"):
                _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        root.handlers.clear()

        # ── Handler Console ─────────────────────────────────────────────
        import io as _io
        import sys as _sys
        debug_env = os.environ.get("DOWFLOW_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        try:
            console_stream = _io.TextIOWrapper(
                _sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
        except Exception:
            console_stream = _sys.stdout
        console = logging.StreamHandler(console_stream)
        console.setLevel(logging.DEBUG if debug_env else logging.INFO)
        console.setFormatter(_ColorFormatter(use_color=True))
        root.addHandler(console)
        if debug_env:
            logging.getLogger("yt_dlp").setLevel(logging.DEBUG)
            logging.getLogger("werkzeug").setLevel(logging.DEBUG)

        # ── Handler Fichier (rotation quotidienne, 30 j) ────────────────
        log_file = LOG_DIR / "dowflow.log"
        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=str(log_file),
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(_FileFormatter())
        file_handler.suffix = "%Y%m%d"  # → dowflow.log.20260908
        root.addHandler(file_handler)

        # ── Réduire le bruit de bibliothèques tierces ─────────────────
        for noisy in ("urllib3", "requests", "playwright", "flask"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        logging.getLogger("werkzeug").setLevel(logging.INFO)
        logging.getLogger("yt_dlp").setLevel(logging.INFO)

        _CONFIGURED = True


# ── API publique ──────────────────────────────────────────────────────────

def get_logger(name: str = "dowflow") -> logging.Logger:
    """Retourne un logger configuré. Même nom = même instance (cache)."""
    _configure()
    with _LOCK:
        if name in _LOGGERS:
            return _LOGGERS[name]
    clean = name.replace("__", "").strip().lower()[:24] or "dowflow"
    lg = logging.getLogger(clean)
    lg.setLevel(logging.DEBUG)
    lg.propagate = True
    with _LOCK:
        _LOGGERS[name] = lg
    return lg


# ── Helper pratique pour taguer une section dans les batchs ──────────────────────────

def banner(log: logging.Logger, title: str) -> None:
    bar = "═" * max(40, len(title) + 4)
    log.info(bar)
    log.info("  %s", title)
    log.info(bar)
