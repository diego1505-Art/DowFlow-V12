"""
DowFlow — Service Musique.

Recherche de morceaux et titres populaires via YouTube (yt-dlp), téléchargement
audio (MP3/FLAC…) et récupération automatique de la pochette d'album
(API Deezer publique, repli sur la miniature YouTube).

L'intégration d'un dataset local est prévue via `music_dataset.get_dataset()` :
s'il est disponible il est interrogé en premier, sinon tout passe par YouTube.
"""
from __future__ import annotations

import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote, urlparse

import requests

from config import AUDIO_EXTENSIONS, DOWNLOAD_FOLDER
from logger import get_logger
from music_dataset import get_dataset
from utils import yt_dlp_runtime_options

log = get_logger("music")

MUSIC_SUBFOLDER = "music"
MUSIC_FOLDER = DOWNLOAD_FOLDER / MUSIC_SUBFOLDER

COVER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
DEEZER_SEARCH_URL = "https://api.deezer.com/search"
_LIBRARY_PROMO_SUFFIX = re.compile(
    r"[-_\s]*(?:official[-_\s]+(?:music[-_\s]+)?video|official[-_\s]+audio|"
    r"lyrics?(?:[-_\s]+video)?|lyric[-_\s]+video|music[-_\s]+video|audio)$",
    re.IGNORECASE,
)

# Requêtes servant à composer la liste « Musiques populaires » (sans dataset).
_POPULAR_QUERIES = (
    "Top hits 2026",
    "musique populaire 2026",
    "Top 50 France 2026",
    "best songs 2026",
)
_POPULAR_TTL_SECONDS = 1800.0
_popular_cache: tuple[float, list[dict[str, Any]]] | None = None


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _thumb_for(video_id: str) -> str:
    """Miniature YouTube standard pour un identifiant vidéo."""
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


def _video_id_from_url(url: str) -> str | None:
    """Extrait l'identifiant vidéo YouTube d'une URL (watch / youtu.be / shorts)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = (parsed.netloc or "").lower().replace("www.", "")
    if "youtu.be" in host:
        candidate = parsed.path.strip("/").split("/")[0]
        return candidate or None
    if "youtube.com" in host:
        if parsed.path.startswith("/watch"):
            from urllib.parse import parse_qs
            values = parse_qs(parsed.query).get("v")
            return values[0] if values else None
        for marker in ("/shorts/", "/embed/", "/v/"):
            if marker in parsed.path:
                return parsed.path.split(marker, 1)[1].split("/")[0] or None
    return None


def _library_track_metadata(stem: str) -> tuple[str, str, str]:
    """Nettoie un nom YouTube et produit une clé stable artiste/titre."""
    raw_artist = ""
    raw_title = stem.strip()
    if "---" in raw_title:
        raw_artist, raw_title = raw_title.split("---", 1)
        raw_artist = re.sub(r"[-_]+", " ", raw_artist).strip()

    raw_title = _LIBRARY_PROMO_SUFFIX.sub("", raw_title).strip(" -_[]()")
    title = re.sub(r"[-_]+", " ", raw_title).strip()
    normalized = unicodedata.normalize(
        "NFKD", f"{raw_artist} {title}"
    ).encode("ascii", "ignore").decode("ascii").lower()
    key = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    return raw_artist, title, key


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Convertit une entrée yt-dlp (flat) en piste exploitable par le frontend."""
    if not isinstance(entry, dict):
        return None
    video_id = entry.get("id")
    if not video_id:
        return None
    raw_url = entry.get("url")
    if isinstance(raw_url, str) and raw_url.startswith("http"):
        url = raw_url
    else:
        url = f"https://www.youtube.com/watch?v={video_id}"
    duration = entry.get("duration")
    return {
        "id": str(video_id),
        "title": entry.get("title") or "Sans titre",
        "artist": entry.get("uploader") or entry.get("channel") or "",
        "url": url,
        "duration": int(duration) if isinstance(duration, (int, float)) else None,
        "thumbnail": _thumb_for(str(video_id)),
        "view_count": entry.get("view_count"),
    }


def _dedupe(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    out: list[dict[str, Any]] = []
    for track in tracks:
        title = (track.get("title") or "").strip()
        artist = (track.get("artist") or "").strip()
        track_id = (track.get("id") or "").strip()

        if track_id and track_id.lower() in seen_ids:
            continue

        if title and artist:
            key = f"{title}|{artist}"
        elif title:
            key = title
        elif track_id:
            key = track_id
        else:
            continue

        norm = "".join(
            ch for ch in key.lower() if ch.isalnum() or ch in {" ", "-", "_", "|"}
        )
        if norm in seen_keys:
            continue
        seen_keys.add(norm)
        if track_id:
            seen_ids.add(track_id.lower())
        out.append(track)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Recherche / titres populaires
# ─────────────────────────────────────────────────────────────────────────────

def _search_youtube(query: str, limit: int) -> list[dict[str, Any]]:
    import yt_dlp

    limit = max(1, min(int(limit or 20), 50))
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": limit,
    }
    opts.update(yt_dlp_runtime_options())

    with yt_dlp.YoutubeDL(opts) as ydl:  # pyright: ignore[reportArgumentType]
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

    entries = (info or {}).get("entries") or []
    tracks = [track for track in (_normalize_entry(e) for e in entries) if track]
    return _dedupe(tracks)[:limit]


def search_music(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Recherche de morceaux : dataset local si présent, sinon YouTube."""
    query = (query or "").strip()
    if not query:
        return []

    dataset = get_dataset()
    if dataset is not None and dataset.available():
        try:
            results = dataset.search(query, limit)
            if results:
                return results[:limit]
        except NotImplementedError:
            pass
        except Exception as exc:  # dataset défaillant → on retombe sur YouTube
            log.warning("[music] Dataset indisponible, repli YouTube : %s", exc)

    try:
        return _search_youtube(query, limit)
    except Exception as exc:
        log.error("[music] Recherche échouée : %s", exc, exc_info=True)
        return []


def popular_music(limit: int = 24) -> list[dict[str, Any]]:
    """Liste de titres populaires (cache mémoire 30 min)."""
    global _popular_cache
    now = time.time()
    if _popular_cache and now - _popular_cache[0] < _POPULAR_TTL_SECONDS:
        return _popular_cache[1][:limit]

    dataset = get_dataset()
    if dataset is not None and dataset.available():
        try:
            result = dataset.popular(limit)
            if result:
                _popular_cache = (now, result)
                return result[:limit]
        except NotImplementedError:
            pass
        except Exception as exc:
            log.warning("[music] Dataset populaire indisponible : %s", exc)

    per_query = max(6, (limit // len(_POPULAR_QUERIES)) + 4)
    tracks: list[dict[str, Any]] = []
    # Les requêtes YouTube sont indépendantes : on les lance en parallèle pour réduire
    # la latence à froid (l'opération la plus lente de l'endpoint /popular).
    with ThreadPoolExecutor(max_workers=len(_POPULAR_QUERIES)) as pool:
        futures = {
            pool.submit(_search_youtube, query, per_query): query
            for query in _POPULAR_QUERIES
        }
        for future in as_completed(futures):
            query = futures[future]
            try:
                tracks.extend(future.result())
            except Exception as exc:
                log.warning("[music] Requête populaire « %s » échouée : %s", query, exc)

    tracks = _dedupe(tracks)[:limit]
    if tracks:
        _popular_cache = (now, tracks)
    return tracks


# ─────────────────────────────────────────────────────────────────────────────
#  Pochettes d'album
# ─────────────────────────────────────────────────────────────────────────────

def fetch_cover_url(artist: str | None, title: str | None) -> str | None:
    """URL de pochette via l'API Deezer (aucune clé requise), sinon None."""
    dataset = get_dataset()
    if dataset is not None and dataset.available():
        try:
            url = dataset.cover_url(artist or "", title or "")
            if url:
                return url
        except Exception:
            pass

    title = (title or "").strip()
    if not title:
        return None

    artist = (artist or "").strip()
    query = " ".join(part for part in (title, artist) if part)
    try:
        response = requests.get(
            DEEZER_SEARCH_URL, params={"q": query, "limit": 1}, timeout=10
        )
        response.raise_for_status()
        items = response.json().get("data") or []
        if items:
            album = items[0].get("album") or {}
            return (
                album.get("cover_xl")
                or album.get("cover_big")
                or album.get("cover_medium")
            )
    except Exception as exc:
        log.debug("[music] Deezer indisponible pour « %s » : %s", title, exc)
    return None


def _save_cover_image(media_path: str, cover_url: str | None, video_id: str | None) -> str | None:
    """Écrit la pochette à côté du fichier audio ; repli miniature YouTube."""
    candidates = [cover_url] if cover_url else []
    if video_id:
        candidates.append(f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg")
        candidates.append(_thumb_for(video_id))

    target = os.path.splitext(media_path)[0] + ".jpg"
    for url in candidates:
        if not url:
            continue
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            if not response.content:
                continue
            with open(target, "wb") as handle:
                handle.write(response.content)
            return target
        except Exception as exc:
            log.debug("[music] Pochette inaccessible (%s) : %s", url[:60], exc)
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Téléchargement
# ─────────────────────────────────────────────────────────────────────────────

def download_music_track(
    url: str,
    output_path: str | None = None,
    audio_format: str = "mp3",
    with_cover: bool = True,
    title: str | None = None,
    artist: str | None = None,
    progress_hook=None,
) -> dict[str, Any]:
    """Télécharge un morceau en audio (+ pochette) dans `downloads/music`."""
    from playlist import download_media

    base_output = output_path or str(DOWNLOAD_FOLDER)
    result = download_media(
        url,
        output_path=base_output,
        download_type="audio",
        audio_format=audio_format,
        progress_hook=progress_hook,
        force_folder=MUSIC_SUBFOLDER,
    )

    filepath = result.get("filepath")
    cover_source = fetch_cover_url(artist, title or result.get("title"))
    if cover_source:
        result["cover_url"] = cover_source

    if filepath and os.path.isfile(filepath):
        video_id = _video_id_from_url(url)
        if with_cover:
            cover_path = _save_cover_image(filepath, cover_source, video_id)
            if cover_path:
                cover_relative = os.path.relpath(cover_path, base_output).replace("\\", "/")
                result["cover_path"] = cover_path
                result["cover_url"] = f"/media/{cover_relative}"
        elif not result.get("cover_path") and cover_source:
            result["cover_path"] = cover_source

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Bibliothèque locale (morceaux déjà téléchargés)
# ─────────────────────────────────────────────────────────────────────────────

def list_library(limit: int = 200) -> list[dict[str, Any]]:
    """Liste les morceaux présents dans `downloads/music` (+ pochette associée)."""
    MUSIC_FOLDER.mkdir(parents=True, exist_ok=True)
    try:
        entries = [p for p in MUSIC_FOLDER.iterdir() if p.is_file()]
    except OSError:
        return []

    entries.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in entries:
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        artist, title, title_key = _library_track_metadata(path.stem)
        if title_key in seen:
            continue
        seen.add(title_key)

        cover_name = None
        for ext in COVER_EXTENSIONS:
            candidate = MUSIC_FOLDER / f"{path.stem}{ext}"
            if candidate.is_file():
                cover_name = candidate.name
                break

        cover_url = (
            f"/media/{MUSIC_SUBFOLDER}/{quote(cover_name)}" if cover_name else None
        )
        if not cover_url:
            cover_url = fetch_cover_url(None, path.stem)

        stat = path.stat()
        items.append(
            {
                "filename": path.name,
                "title": title or path.stem,
                "artist": artist,
                "audio_url": f"/media/{MUSIC_SUBFOLDER}/{quote(path.name)}",
                "cover_url": cover_url,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )

    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items[:limit]


def delete_track(filename: str) -> bool:
    """Supprime un morceau et sa pochette associée."""
    from werkzeug.utils import secure_filename
    safe_name = secure_filename(filename)
    audio_path = MUSIC_FOLDER / safe_name
    
    if not audio_path.is_file():
        return False
        
    try:
        # Supprimer l'audio
        audio_path.unlink()
        
        # Supprimer la pochette si elle existe (même nom de base)
        base_name = audio_path.stem
        for ext in COVER_EXTENSIONS:
            cover_path = MUSIC_FOLDER / f"{base_name}{ext}"
            if cover_path.is_file():
                cover_path.unlink()
        return True
    except Exception as exc:
        log.error("[music] Erreur lors de la suppression de %s : %s", filename, exc)
        return False
