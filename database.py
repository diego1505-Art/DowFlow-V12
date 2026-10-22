"""
DowFlow — Couche de persistance SQLite (stdlib, zéro dépendance).

Tables :
    downloads         : historique des téléchargements (succès/échec)
    site_strategy     : statistiques par domaine
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any

from config import BASE_DIR

DB_PATH = BASE_DIR / "dowflow.db"

_LOCK = threading.Lock()
_SCHEMA_VERSION = 1


# ─────────────────────────────────────────────────────────────────────────────
#  Utilitaires bas niveau
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def _conn():
    """Connexion thread-safe avec row_factory activée."""
    if not _LOCK.acquire(timeout=10):
        raise TimeoutError("Délai dépassé lors de l'attente du verrou SQLite.")
    try:
        con = sqlite3.connect(str(DB_PATH), timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA foreign_keys=ON;")
        try:
            yield con
            con.commit()
        finally:
            con.close()
    finally:
        _LOCK.release()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if value is not None else "{}"


def _json_loads(raw: str | None) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
#  Schéma
# ─────────────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Crée les tables et les index si elles n'existent pas. Idempotent."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.executescript(
            """
            -- Table 1 : Historique téléchargements
            CREATE TABLE IF NOT EXISTS downloads (
                id              INTEGER PRIMARY
                  KEY AUTOINCREMENT,
                url             TEXT    NOT NULL,
                domain          TEXT,
                extractor_type  TEXT,           -- yt-dlp / franime / browser
                player_name     TEXT,           -- SIBNET / HLS / FILEMOON / etc.
                download_type   TEXT,           -- video / audio
                quality         TEXT,           -- 1080p / best / etc.
                status          TEXT NOT NULL,  -- success / failed / skipped / running
                error_message   TEXT,
                file_path       TEXT,
                file_size_bytes INTEGER,
                duration_ms     INTEGER,
                started_at      REAL    NOT NULL,
                finished_at     REAL,
                metadata_json   TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_downloads_domain  ON downloads(domain);
            CREATE INDEX IF NOT EXISTS idx_downloads_status  ON downloads(status);
            CREATE INDEX IF NOT EXISTS idx_downloads_started ON downloads(started_at);

            -- Table 2 : Stratégie par site (mémoire de DowFlow)
            CREATE TABLE IF NOT EXISTS site_strategy (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                domain              TEXT    NOT NULL UNIQUE,
                url_pattern         TEXT,                     -- regex optionnel
                preferred_extractor TEXT,                     -- franime / yt-dlp / browser
                preferred_player    TEXT,                     -- SIBNET / HLS / etc.
                preferred_quality   TEXT,                     -- 1080p / best
                video_format_ok     TEXT,                     -- mp4 / mkv
                audio_format_ok     TEXT,                     -- mp3
                success_count       INTEGER NOT NULL DEFAULT 0,
                fail_count          INTEGER NOT NULL DEFAULT 0,
                last_used_at        REAL,
                avg_duration_ms     INTEGER,
                anti_bot_steps_json TEXT DEFAULT '[]',
                extra_json          TEXT DEFAULT '{}',
                updated_at          REAL    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_site_strategy_domain ON site_strategy(domain);
            CREATE INDEX IF NOT EXISTS idx_site_strategy_rate
                ON site_strategy((success_count - fail_count) DESC);

            -- Meta : version schéma
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )

        c.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
            ("schema_version", str(_SCHEMA_VERSION)),
        )


def db_is_ready() -> bool:
    return DB_PATH.is_file() and os.path.getsize(DB_PATH) > 0


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD downloads
# ─────────────────────────────────────────────────────────────────────────────

def download_start(
    url: str,
    domain: str | None = None,
    download_type: str = "video",
    quality: str = "best",
) -> int:
    """Insère un nouveau download en statut 'running' → retourne son id."""
    from urllib.parse import urlparse
    dom = domain or (urlparse(url).netloc.lower().replace("www.", "") if url else "")
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO downloads(url, domain, download_type, quality,
                                     status, started_at)
               VALUES (?, ?, ?, ?, 'running', ?)""",
            (url, dom, download_type, quality, time.time()),
        )
        return int(cur.lastrowid)


def download_finish(
    download_id: int,
    status: str,  # success / failed / skipped
    extractor_type: str | None = None,
    player_name: str | None = None,
    error_message: str | None = None,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    duration_ms: int | None = None,
    metadata: dict | None = None,
) -> None:
    """Finalise un download (apprentissage déclenché en auto)."""
    finished = time.time()
    with _conn() as c:
        row = c.execute(
            "SELECT url, domain, started_at FROM downloads WHERE id=?",
            (download_id,),
        ).fetchone()

        c.execute(
            """UPDATE downloads
                  SET status         = ?,
                      extractor_type = COALESCE(?, extractor_type),
                      player_name    = COALESCE(?, player_name),
                      error_message  = ?,
                      file_path      = ?,
                      file_size_bytes= ?,
                      duration_ms    = ?,
                      finished_at    = ?,
                      metadata_json  = COALESCE(?, metadata_json)
                WHERE id = ?""",
            (
                status,
                extractor_type,
                player_name,
                error_message,
                file_path,
                file_size_bytes,
                duration_ms,
                finished,
                _json_dumps(metadata or {}),
                download_id,
            ),
        )

        if row:
            started = float(row["started_at"])
            dur_ms = int(max(0, (finished - started) * 1000))
            domain = row["domain"] or ""
            if domain:
                _learn_from_result(
                    c,
                    domain=domain,
                    status=status,
                    extractor_type=extractor_type,
                    player_name=player_name,
                    duration_ms=dur_ms,
                )


def download_list(limit: int = 100, status: str | None = None) -> list[dict]:
    """Retourne les derniers downloads sous forme de liste de dicts."""
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM downloads WHERE status=? ORDER BY started_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM downloads ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["metadata"] = _json_loads(d.get("metadata_json"))
        out.append(d)
    return out


def download_count_by_status() -> dict[str, int]:
    """Stats rapide : {'success': 42, 'failed': 3, 'skipped': 5, 'running': 0}."""
    with _conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM downloads GROUP BY status"
        ).fetchall()
    return {r["status"]: int(r["n"]) for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
#  Apprentissage auto : site_strategy (mise à jour à chaque download fini)
# ─────────────────────────────────────────────────────────────────────────────

def _learn_from_result(
    c: sqlite3.Connection,
    domain: str,
    status: str,
    extractor_type: str | None,
    player_name: str | None,
    duration_ms: int,
) -> None:
    """Met à jour les stats par domaine. Est appelé depuis download_finish()."""
    if not domain:
        return

    row = c.execute(
        "SELECT id, success_count, fail_count, avg_duration_ms FROM site_strategy WHERE domain=?",
        (domain,),
    ).fetchone()

    is_success = 1 if status == "success" else 0
    is_fail = 1 if status == "failed" else 0

    if row is None:
        c.execute(
            """INSERT INTO site_strategy
               (domain, preferred_extractor, preferred_player,
                success_count, fail_count, last_used_at,
                avg_duration_ms, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                domain,
                extractor_type,
                player_name,
                is_success,
                is_fail,
                time.time(),
                duration_ms or None,
                time.time(),
            ),
        )
        return

    sid = int(row["id"])
    new_s = int(row["success_count"]) + is_success
    new_f = int(row["fail_count"]) + is_fail
    old_avg = row["avg_duration_ms"] or 0
    if duration_ms and is_success:
        if old_avg:
            new_avg = int((old_avg + duration_ms) / 2)
        else:
            new_avg = duration_ms
    else:
        new_avg = old_avg

    updates: list[str] = [
        "success_count = ?",
        "fail_count = ?",
        "last_used_at = ?",
        "avg_duration_ms = ?",
        "updated_at = ?",
    ]
    params: list[Any] = [new_s, new_f, time.time(), new_avg, time.time()]

    if extractor_type and is_success:
        updates.append("preferred_extractor = ?")
        params.append(extractor_type)
    if player_name and is_success:
        updates.append("preferred_player = ?")
        params.append(player_name)

    params.append(sid)
    c.execute(f"UPDATE site_strategy SET {', '.join(updates)} WHERE id=?", params)


# ─────────────────────────────────────────────────────────────────────────────
#  Auto-init à l'import (si la DB n'existe pas encore)
# ─────────────────────────────────────────────────────────────────────────────
init_db()
