"""Expansion YouTube (playlist / liste plate via yt-dlp)."""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from .base import ExpandResult, OptionDef, SiteProfile
from utils import yt_dlp_runtime_options

YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}


def supports(url: str, host: str) -> bool:
    return host in YOUTUBE_HOSTS or host.endswith(".youtube.com")


def profile_for(url: str, host: str) -> SiteProfile | None:
    if not supports(url, host):
        return None

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    is_playlist = "list" in query or "/playlist" in parsed.path
    is_channel = any(p in parsed.path for p in ("/channel/", "/@", "/c/", "/user/"))

    if is_playlist:
        help_text = "Playlist YouTube détectée. Trouver extrait les vidéos de la playlist."
    elif is_channel:
        help_text = "Chaîne YouTube détectée. Trouver peut lister les vidéos récentes."
    else:
        help_text = (
            "Vidéo YouTube seule. Coche « Étendre si playlist » si l'URL contient un paramètre list=."
        )

    return SiteProfile(
        site_id="youtube",
        label="YouTube",
        available=True,
        help_text=help_text,
        options=[
            OptionDef(
                key="expand_playlist",
                label="Étendre la playlist",
                kind="toggle",
                default=True if is_playlist else bool(query.get("list")),
                help_text="Remplace l'URL par toutes les vidéos de la playlist associée.",
            ),
            OptionDef(
                key="expand_channel",
                label="Lister la chaîne",
                kind="toggle",
                default=False,
                enabled=is_channel,
                help_text="Disponible seulement pour une URL de chaîne / @handle.",
            ),
            OptionDef(
                key="max_items",
                label="Limite d'éléments",
                kind="number",
                default=50,
                min_value=1,
                max_value=500,
                help_text="Nombre max d'URLs à générer (protège contre les énormes playlists).",
            ),
        ],
    )


def expand(url: str, options: dict[str, Any]) -> ExpandResult:
    expand_playlist = bool(options.get("expand_playlist", True))
    expand_channel = bool(options.get("expand_channel", False))
    try:
        max_items = int(options.get("max_items") or 50)
    except (TypeError, ValueError):
        max_items = 50
    max_items = max(1, min(500, max_items))

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    has_list = "list" in query or "/playlist" in parsed.path
    is_channel = any(p in parsed.path for p in ("/channel/", "/@", "/c/", "/user/"))

    # ── Cas "rien à étendre" : retourner simplement l'URL telle quelle
    #    au lieu d'une erreur 400. Améliore énormément l'UX.
    has_real_work = (expand_playlist and has_list) or (expand_channel and is_channel)
    if not has_real_work:
        return ExpandResult(
            success=True,
            site_id="youtube",
            urls=[url],
            count=1,
            title="URL YouTube unique (pas de playlist / chaîne détectée)",
        )

    try:
        import yt_dlp
    except ImportError:
        return ExpandResult(success=False, site_id="youtube", error="yt-dlp non installé.")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": max_items,
    }
    ydl_opts.update(yt_dlp_runtime_options())

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return ExpandResult(success=False, site_id="youtube", error=f"yt-dlp: {exc}")

    if not info:
        return ExpandResult(success=False, site_id="youtube", error="Aucune info retournée.")

    entries = info.get("entries") or []
    urls: list[str] = []
    for entry in entries:
        if not entry:
            continue
        vid = entry.get("url") or entry.get("id")
        if not vid:
            continue
        if isinstance(vid, str) and vid.startswith("http"):
            urls.append(vid)
        else:
            urls.append(f"https://www.youtube.com/watch?v={vid}")
        if len(urls) >= max_items:
            break

    if not urls and info.get("id") and info.get("_type") != "playlist":
        # Single video
        urls = [f"https://www.youtube.com/watch?v={info['id']}"]

    if not urls:
        return ExpandResult(success=False, site_id="youtube", error="Aucune vidéo trouvée.")

    title = info.get("title") or "YouTube"
    return ExpandResult(
        success=True,
        urls=urls,
        count=len(urls),
        title=f"{title} ({len(urls)} éléments)",
        site_id="youtube",
    )
