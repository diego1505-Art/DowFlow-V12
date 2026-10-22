import os
import re
import shutil
import time
from typing import Any, Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import yt_dlp

from config import (
    VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS,
    VIDEO_OUTPUT_FORMATS,
    AUDIO_OUTPUT_FORMATS,
    VIDEO_QUALITIES,
    LANGUAGES,
    YOUTUBE_COOKIES_FILE,
    normalize_language,
    detect_language_from_url,
)
from utils import _resolve_ffmpeg_dir, yt_dlp_runtime_options
from logger import get_logger
from database import download_start, download_finish

log = get_logger("playlist")

IGNORED_EXTENSIONS = {".part", ".ytdl", ".jpg", ".jpeg", ".png", ".webp", ".description"}
VALID_DOWNLOAD_TYPES = {"audio", "video"}
CONTAINER_SEGMENTS = {"anime", "watch", "video", "videos", "embed", "episode", "show"}

# ── Backward-compat : map anciennes clés de qualité vers VIDEO_QUALITIES ─────
_LEGACY_QUALITY_MAP = {
    "best": "best",
    "ultra": "2160p",
    "qhd": "1440p",
    "high": "1080p",
    "medium": "720p",
    "low": "480p",
    "360": "360p",
    "240": "240p",
}

_ADAPTIVE_QUALITY_ORDER = ["4320p", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p"]
_ADAPTIVE_TIME_THRESHOLDS = {
    "4320p": 180,  # 3 min
    "2160p": 300,  # 5 min
    "1440p": 600,  # 10 min
    "1080p": 900,  # 15 min
    "720p": 1200,  # 20 min
    "480p": 1500,  # 25 min
    "360p": 1800,  # 30 min
    "240p": 2100,  # 35 min
}

# Surveillance temps réel : délai d'observation avant de décider d'un downgrade.
_ADAPTIVE_GRACE_SECONDS = 10.0


class _AdaptiveDowngrade(BaseException):
    """Interrompt le téléchargement pour le relancer à une qualité inférieure.

    Dérive de BaseException (et non Exception) pour ne pas être avalée par les
    `except Exception` internes de yt-dlp : seule la boucle de `_ydlp_download`
    doit l'intercepter.
    """

    def __init__(self, quality: str, note: str) -> None:
        super().__init__(note)
        self.quality = quality
        self.note = note


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "media"


def _friendly_cookie_error(error: Exception) -> str | None:
    message = str(error).lower()
    if "could not copy chrome cookie database" in message or "permission denied" in message and "cookies" in message:
        return (
            "Windows bloque la lecture des cookies de votre profil Chrome. Pour garder votre "
            "navigateur ouvert, exportez les cookies (YouTube/Instagram) au format Netscape dans "
            "youtube_cookies.txt à la racine du projet. Ce fichier donne accès à votre compte : "
            "ne le partagez pas et ne le publiez pas."
        )
    if any(t in message for t in ("sign in to confirm", "not a bot", "login", "log in", "inaccessible")):
        return (
            "Le site demande une session connectée ou l'accès est bloqué. Connectez-vous sur le site "
            "dans votre navigateur Chrome, ou fournissez un fichier youtube_cookies.txt valide."
        )
    return None


def _resolve_cookie_file() -> str | None:
    """Recherche un fichier de cookies Netscape (youtube_cookies.txt par défaut)."""
    configured_path = os.environ.get("DOWFLOW_YOUTUBE_COOKIES")
    cookie_path = Path(configured_path).expanduser() if configured_path else YOUTUBE_COOKIES_FILE
    return str(cookie_path) if cookie_path.is_file() else None


def _needs_session(error: Exception) -> bool:
    """Détecte si le site exige une session connectée ou si l'accès est bloqué."""
    message = str(error).lower()
    return any(
        token in message
        for token in (
            "sign in to confirm",
            "not a bot",
            "could not copy chrome cookie database",
            "cookie",
            "login",
            "log in",
            "inaccessible",
            "inaccessibles",
            "forbidden",
            "403",
        )
    )


def _cookie_retry_options(ydl_opts: dict[str, Any], cookie_file: str | None) -> dict[str, Any]:
    """Options de repli avec session YouTube : fichier Netscape, sinon profil Chrome."""
    retry_opts = dict(ydl_opts)
    retry_opts.pop("cookiesfrombrowser", None)
    if cookie_file:
        retry_opts["cookiefile"] = cookie_file
    else:
        retry_opts.update(yt_dlp_runtime_options(include_browser_cookies=True))
    return retry_opts


def _derive_series_slug(parsed_url) -> str:
    raw_segments = [segment for segment in parsed_url.path.split("/") if segment.strip()]
    segments = [_slugify(segment) for segment in raw_segments if _slugify(segment)]
    if not segments:
        return "remote"
    if len(segments) >= 2 and segments[0] in CONTAINER_SEGMENTS:
        return segments[1]
    return segments[-1]


def _build_output_folder(url: str, base_output_path: str) -> str:
    """Crée un dossier de sortie basé sur le domaine (youtube, instagram, twitter, etc.)."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "") or "remote"

    # Nettoyage du nom de domaine pour le dossier (ex: youtube.com -> youtube)
    folder_name = domain.split(".")[0] if "." in domain else domain
    target_dir = os.path.join(base_output_path, folder_name)

    # Pour les sites complexes (anime, etc.), on peut garder une structure plus profonde
    query = parse_qs(parsed.query)
    stable_id = None
    for key in ("anime_id", "series_id", "show_id", "id"):
        values = query.get(key)
        if values and values[0].strip():
            stable_id = _slugify(values[0])
            break

    if stable_id:
        return os.path.join(target_dir, f"id-{stable_id}")

    return target_dir


def _relative_download_path(filepath: str, base_output_path: str) -> str:
    return os.path.relpath(filepath, start=base_output_path).replace("\\", "/")


def _collect_created_files(output_path: str, before_files: set[str]) -> list[str]:
    created_files: list[str] = []
    for name in os.listdir(output_path):
        full_path = os.path.join(output_path, name)
        if os.path.isfile(full_path) and name not in before_files:
            created_files.append(full_path)
    created_files.sort(key=os.path.getmtime, reverse=True)
    return created_files


def _candidate_filepaths_from_info(info: dict[str, Any], output_path: str) -> list[str]:
    candidates: list[str] = []

    requested_downloads = info.get("requested_downloads")
    if isinstance(requested_downloads, list):
        for item in requested_downloads:
            if isinstance(item, dict):
                filepath = item.get("filepath")
                if isinstance(filepath, str) and filepath:
                    candidates.append(filepath)

    for key in ("filepath", "_filename"):
        value = info.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)

    title = info.get("title")
    ext = info.get("ext")
    if isinstance(title, str) and title:
        if isinstance(ext, str) and ext:
            candidates.append(os.path.join(output_path, f"{title}.{ext}"))
        for known_ext in sorted(VIDEO_EXTENSIONS | AUDIO_EXTENSIONS):
            candidates.append(os.path.join(output_path, f"{title}{known_ext}"))

    unique_candidates: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        normalized = os.path.normpath(path)
        if normalized not in seen:
            unique_candidates.append(normalized)
            seen.add(normalized)

    return unique_candidates


def _pick_media_file(created_files: list[str], download_type: str) -> str | None:
    allowed_exts = AUDIO_EXTENSIONS if download_type == "audio" else VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

    for path in created_files:
        suffix = os.path.splitext(path)[1].lower()
        if suffix in IGNORED_EXTENSIONS:
            continue
        if suffix in allowed_exts:
            return path

    for path in created_files:
        suffix = os.path.splitext(path)[1].lower()
        if suffix not in IGNORED_EXTENSIONS:
            return path

    return None


def _resolve_downloaded_file(info: dict[str, Any], created_files: list[str], output_path: str, download_type: str) -> str | None:
    for candidate in _candidate_filepaths_from_info(info, output_path):
        if os.path.isfile(candidate):
            suffix = os.path.splitext(candidate)[1].lower()
            if suffix not in IGNORED_EXTENSIONS:
                return candidate

    return _pick_media_file(created_files, download_type)


# ──────────────────────────────────────────────────────────────────────────────
# NOUVEAU : Construction du format yt-dlp à partir des nouvelles options
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_quality_key(raw_quality: str) -> str:
    """Convertit une clé de qualité (legacy ou nouvelle) vers la clé canonique VIDEO_QUALITIES."""
    q = (raw_quality or "best").strip().lower()
    if q in VIDEO_QUALITIES:
        return q
    if q in _LEGACY_QUALITY_MAP:
        return _LEGACY_QUALITY_MAP[q]
    # ex: "1080" -> "1080p"
    if q.rstrip("p") in {str(k.rstrip("p")) for k in VIDEO_QUALITIES}:
        for k in VIDEO_QUALITIES:
            if k.rstrip("p") == q.rstrip("p"):
                return k
    return "best"


def _measure_network_speed_mbps(url: str = "https://www.google.com/favicon.ico", timeout_seconds: float = 5.0) -> float:
    """Mesure un débit réseau de base pour choisir la qualité admissible."""
    try:
        # On utilise Google ou YouTube pour une mesure plus stable
        start = time.time()
        with urlopen(url, timeout=timeout_seconds) as resp:
            # On lit un peu plus de données (1 Mo) pour lisser les pics
            chunk = resp.read(1024 * 1024)
            elapsed = max(time.time() - start, 0.001)
        bytes_received = max(len(chunk), 1)
        # Retourne le débit réel sans facteur de sécurité excessif
        return (bytes_received * 8.0 / (elapsed * 1000000.0))
    except Exception:
        return 0.0


def _tier_for_resolution(resolution: int) -> str | None:
    """Palier de qualité (240p → 8K) correspondant à une résolution vidéo réelle."""
    if not resolution or resolution <= 0:
        return None
    for key in reversed(_ADAPTIVE_QUALITY_ORDER):  # du plus bas au plus haut
        max_height = VIDEO_QUALITIES.get(key, {}).get("max_height")
        if max_height and resolution <= max_height:
            return key
    return None


def _next_lower_quality(quality_key: str) -> str | None:
    """Palier immédiatement inférieur, ou None si on est déjà au plus bas."""
    try:
        index = _ADAPTIVE_QUALITY_ORDER.index(quality_key)
    except ValueError:
        return None
    if index + 1 >= len(_ADAPTIVE_QUALITY_ORDER):
        return None
    return _ADAPTIVE_QUALITY_ORDER[index + 1]


def _progress_resolution(progress: dict[str, Any]) -> int:
    """Résolution (min(largeur, hauteur)) du flux en cours de téléchargement."""
    info = progress.get("info_dict") or {}
    values = [
        value
        for value in (
            info.get("width"),
            info.get("height"),
            progress.get("width"),
            progress.get("height"),
        )
        if isinstance(value, int) and value > 0
    ]
    return min(values) if values else 0


def _make_adaptive_watch_hook(user_hook: Callable[[dict[str, Any]], None] | None = None) -> Callable[[dict[str, Any]], None]:
    """Hook de progression qui mesure le débit réel et coupe si l'ETA est trop longue.

    Pendant les `_ADAPTIVE_GRACE_SECONDS` premières secondes, on observe le débit
    réel du flux vidéo. Au-delà, si l'ETA projetée dépasse le seuil du palier en
    cours, on lève `_AdaptiveDowngrade` pour relancer à la qualité inférieure.
    """
    state: dict[str, Any] = {
        "filename": None,
        "started_at": None,
        "start_bytes": 0,
        "decided": False,
    }

    def hook(progress: dict[str, Any]) -> None:
        if user_hook is not None:
            user_hook(progress)
        if progress.get("status") != "downloading" or state["decided"]:
            return

        tier = _tier_for_resolution(_progress_resolution(progress))
        if tier is None:
            return  # flux audio ou résolution inconnue : aucune décision à prendre

        next_quality = _next_lower_quality(tier)
        if next_quality is None:
            state["decided"] = True  # déjà au palier le plus bas
            return

        now = time.time()
        filename = progress.get("filename")
        if state["filename"] != filename:
            # Nouveau flux : on (re)lance la fenêtre d'observation.
            state["filename"] = filename
            state["started_at"] = now
            state["start_bytes"] = progress.get("downloaded_bytes") or 0
            return

        elapsed = now - (state["started_at"] or now)
        if elapsed < _ADAPTIVE_GRACE_SECONDS:
            return

        total = progress.get("total_bytes") or progress.get("total_bytes_estimate") or 0
        done = progress.get("downloaded_bytes") or 0
        transferred = done - state["start_bytes"]
        if total <= 0 or transferred <= 0:
            return

        speed = transferred / elapsed
        eta = max(total - done, 0) / speed
        threshold = _ADAPTIVE_TIME_THRESHOLDS.get(tier)
        state["decided"] = True
        if threshold and eta > threshold:
            raise _AdaptiveDowngrade(
                next_quality,
                f"Débit limité détecté (ETA {int(eta)}s en {tier}) → passage en {next_quality}",
            )

    return hook


def _cleanup_partial_files(output_path: str, before_files: set[str]) -> None:
    """Supprime les fichiers partiels d'une tentative abandonnée (downgrade)."""
    try:
        names = os.listdir(output_path)
    except OSError:
        return
    for name in names:
        if name in before_files:
            continue
        if name.lower().endswith((".part", ".ytdl", ".aria2", ".frag")):
            try:
                os.remove(os.path.join(output_path, name))
            except OSError:
                pass


def _build_video_format_string(
    quality_key: str,
    video_format_key: str,
    has_ffmpeg: bool,
    prefer_http: bool = False,
) -> str:
    """Construit le `format` yt-dlp pour la vidéo en fonction de la qualité + conteneur.

    La résolution cible n'est pas filtrée ici (un filtre `height<=` échoue sur les
    vidéos verticales : 256/426/1024 au lieu de 144/240/480) ; elle est appliquée via
    `_build_video_format_sort` (`res:240` = min(largeur, hauteur)).
    """
    f_meta = VIDEO_OUTPUT_FORMATS.get(video_format_key, VIDEO_OUTPUT_FORMATS["best"])
    v_ext = f_meta.get("vext") or f_meta.get("vcodec")
    a_ext = f_meta.get("aext") or f_meta.get("acodec")
    container = f_meta["container"]
    protocol_filter = "[protocol^=http]" if prefer_http else ""

    if not has_ffmpeg:
        # Pas de merge possible → format single-file avec conteneur MP4 de préférence
        ext_filter = f"[ext={container}]" if container else ""
        return (
            f"best{ext_filter}{protocol_filter}[acodec!=none]/"
            f"best{protocol_filter}[acodec!=none]/"
            f"best[acodec!=none]/best"
        )

    # Avec ffmpeg : bestvideo + bestaudio
    v_ext_f = (f"[ext={v_ext}]" if v_ext else "") + protocol_filter
    a_ext_f = (f"[ext={a_ext}]" if a_ext else "") + protocol_filter

    best_split = f"bestvideo{v_ext_f}+bestaudio{a_ext_f}"
    # Fallback moins restrictif si l'extension/codec n'existe pas
    fallback_split = f"bestvideo{protocol_filter}+bestaudio{protocol_filter}"
    # Dernier recours : fichier single
    ext_fallback = f"[ext={container}]" if container else ""
    fallback_single = (
        f"best{ext_fallback}{protocol_filter}[acodec!=none]/"
        f"best{protocol_filter}[acodec!=none]/best[acodec!=none]/best"
    )
    # Recours universel (défaut yt-dlp) : couvre les vidéos sans format muxué
    # ni flux séparés exploitables (ex. certains Shorts).
    universal_fallback = "bestvideo*+bestaudio/best"

    return f"{best_split}/{fallback_split}/{fallback_single}/{universal_fallback}"


def _build_video_format_sort(quality_key: str) -> list[str] | None:
    """Tri de résolution ciblé pour la qualité demandée.

    `res` vaut min(largeur, hauteur) : le tri reste correct pour les vidéos
    verticales (Shorts), là où un filtre sur `height` ne trouve aucun flux.
    """
    q_key = _resolve_quality_key(quality_key)
    max_height = VIDEO_QUALITIES.get(q_key, VIDEO_QUALITIES["best"])["max_height"]
    return [f"res:{max_height}"] if max_height else None


def _build_audio_format_string(audio_format_key: str) -> str:
    """Format yt-dlp pour l'extraction audio."""
    f_meta = AUDIO_OUTPUT_FORMATS.get(audio_format_key, AUDIO_OUTPUT_FORMATS["best"])
    codec = f_meta["codec"]
    if codec is None:
        return "bestaudio/best"
    # On préfère un format source compatible puis conversion via postprocessor FFmpeg
    return f"bestaudio[ext={codec}]/bestaudio/best"


def _build_audio_postprocessor(audio_format_key: str, has_ffmpeg: bool) -> dict[str, Any] | None:
    """Post-processeur FFmpegExtractAudio (retourne None si pas de ffmpeg ou format 'best')."""
    if not has_ffmpeg:
        return None
    f_meta = AUDIO_OUTPUT_FORMATS.get(audio_format_key, AUDIO_OUTPUT_FORMATS["best"])
    codec = f_meta["codec"]
    if codec is None:
        return None
    quality = f_meta["quality"]
    pp: dict[str, Any] = {
        "key": "FFmpegExtractAudio",
        "preferredcodec": codec,
    }
    if quality and quality > 0:
        pp["preferredquality"] = str(quality)
    return pp


def _build_subtitle_lang_options(lang_code: str | None) -> dict[str, Any]:
    """Options yt-dlp pour sous-titres / audio en fonction de la langue choisie."""
    opts: dict[str, Any] = {
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "vtt/best",
    }
    if not lang_code:
        opts["subtitleslangs"] = ["fr"]
        return opts

    if lang_code == "multi":
        opts["subtitleslangs"] = ["all"]
        return opts

    codes = {lang_code, *LANGUAGES.get(lang_code, {}).get("aliases", set())}
    # YouTube : codes courts + variantes regionales (zh-Hans, pt-BR…)
    ytdlp_codes = sorted(
        {
            c
            for c in codes
            if c not in {"vf", "vo", "multi"} and 2 <= len(c) <= 10
        }
    )
    if lang_code == "zh":
        ytdlp_codes = sorted(set(ytdlp_codes) | {"zh", "zh-Hans", "zh-Hant", "zh-CN", "zh-TW"})
    if not ytdlp_codes:
        opts["subtitleslangs"] = ["all"]
        return opts

    opts["subtitleslangs"] = ytdlp_codes
    return opts


def _collect_subtitle_sidecars(
    media_filepath: str | None,
    created_files: list[str],
    output_path: str,
) -> list[str]:
    """Trouve les .srt/.vtt/.ass telecharges a cote de la video et les aligne sur son nom."""
    if not media_filepath or not os.path.isfile(media_filepath):
        return []

    sub_exts = {".srt", ".vtt", ".ass"}
    media_dir = os.path.dirname(media_filepath)
    media_stem = os.path.splitext(os.path.basename(media_filepath))[0]
    candidates: list[str] = []

    for path in created_files:
        ext = os.path.splitext(path)[1].lower()
        if ext in sub_exts and os.path.isfile(path):
            candidates.append(path)

    # Aussi scanner le dossier (yt-dlp peut ecrire apres le snapshot)
    try:
        for name in os.listdir(media_dir):
            full = os.path.join(media_dir, name)
            if not os.path.isfile(full):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in sub_exts and full not in candidates:
                # Garder ceux qui partagent une racine proche du media (avant/apres slugify)
                base = os.path.splitext(name)[0]
                if media_stem[:20] in base or base[:20] in media_stem or _slugify_filename(base).startswith(media_stem[:16]):
                    candidates.append(full)
    except OSError:
        pass

    aligned: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        ext = os.path.splitext(path)[1].lower()
        # Ex: video.zh-Hans.srt → garder le code langue dans le nom final
        raw_stem = os.path.splitext(os.path.basename(path))[0]
        lang_suffix = ""
        parts = raw_stem.rsplit(".", 1)
        if len(parts) == 2 and 2 <= len(parts[1]) <= 10 and parts[1].replace("-", "").isalnum():
            lang_suffix = f".{parts[1]}"
        target = os.path.join(media_dir, f"{media_stem}{lang_suffix}{ext}")
        try:
            if os.path.normpath(path) != os.path.normpath(target):
                if os.path.exists(target):
                    # collision : garder un suffixe unique
                    n = 2
                    while os.path.exists(os.path.join(media_dir, f"{media_stem}{lang_suffix}-{n}{ext}")):
                        n += 1
                    target = os.path.join(media_dir, f"{media_stem}{lang_suffix}-{n}{ext}")
                os.rename(path, target)
            if target not in seen:
                aligned.append(target)
                seen.add(target)
        except OSError:
            if path not in seen and os.path.isfile(path):
                aligned.append(path)
                seen.add(path)
    return aligned


# ──────────────────────────────────────────────────────────────────────────────
# Point d'entrée principal (signature étendue, rétro-compat)
# ──────────────────────────────────────────────────────────────────────────────

def download_media(
    url,
    output_path="downloads",
    download_type="video",
    quality="best",
    video_format="mp4",
    audio_format="mp3",
    language: str | None = None,
    write_subtitles: bool = False,
    progress_hook=None,
    adaptive_quality: bool = True,
    force_folder: str | None = None,
) -> dict[str, Any]:
    """
    Point d'entrée unique :
      1. franime.fr  → extracteur dédié
      2. Sites yt-dlp → yt-dlp direct
      3. Autres       → extracteur navigateur générique

    Paramètres nouveaux (compatibilité ascendante conservée) :
      video_format   : clé dans VIDEO_OUTPUT_FORMATS (best/mp4/mkv/webm/mov/avi)
      audio_format   : clé dans AUDIO_OUTPUT_FORMATS (best/mp3/mp3_320/flac/wav/…)
      language       : code langue ISO (vf/vo/fr/en/es/…) ou None → auto
      write_subtitles: bool — active le téléchargement de sous-titres si disponible
      force_folder   : sous-dossier de sortie imposé (ex: "music") au lieu du
                       classement automatique par domaine
    """
    import re as _re

    download_type = str(download_type).lower().strip()
    quality = str(quality).lower().strip()

    if download_type not in VALID_DOWNLOAD_TYPES:
        download_type = "video"

    # Fallback auto langue : détection via URL si non fournie
    resolved_lang = normalize_language(language) if language else detect_language_from_url(url)

    base_output_path = output_path
    if force_folder:
        target_output_path = os.path.join(base_output_path, force_folder)
    else:
        target_output_path = _build_output_folder(url, base_output_path)
    os.makedirs(target_output_path, exist_ok=True)

    # ── Début tracking BDD ────────────────────────────────────────────────────
    parsed_global = urlparse(url)
    domain_global = parsed_global.netloc.lower().replace("www.", "") or "remote"
    dl_id = download_start(url, domain=domain_global, download_type=download_type, quality=quality)
    log.info("Nouveau téléchargement #%d — %s", dl_id, url[:100])

    # ── Check existance ──────────────────────────────────────────────────────
    if _re.match(r"https?://(?:www\.)?franime\.fr/", url, _re.I):
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        slug = parsed.path.split('/')[-1] or "video"
        s = params.get('s', ['1'])[0]
        ep = params.get('ep', ['1'])[0]
        lang_tag = resolved_lang or params.get('lang', ['vo'])[0]
        filename_base = f"{slug}-s{s}-ep{ep}-{lang_tag}"

        for ext in VIDEO_EXTENSIONS | {".mp4", ".mkv", ".webm"}:
            potential_file = os.path.join(target_output_path, f"{filename_base}{ext}")
            if os.path.isfile(potential_file):
                log.warning("[skip] Déjà téléchargé : %s", os.path.basename(potential_file))
                fsize = os.path.getsize(potential_file)
                download_finish(
                    dl_id, "skipped",
                    error_message="already_exists",
                    file_path=potential_file,
                    file_size_bytes=fsize,
                    metadata={"reason": "already_exists"},
                )
                return {
                    "success": True,
                    "skipped": True,
                    "reason": "already_exists",
                    "filename": os.path.basename(potential_file),
                    "filepath": potential_file,
                }

    extractor_used: str | None = None
    player_used: str | None = None

    # ── 1. Franime ───────────────────────────────────────────────────────────
    if _re.match(r"https?://(?:www\.)?franime\.fr/", url, _re.I):
        extractor_used = "franime"
        try:
            from franime_extractor import download_franime
            result = download_franime(
                url,
                output_path=target_output_path,
                download_type=download_type,
                quality=quality,
                video_format=video_format,
                audio_format=audio_format,
                language=resolved_lang,
            )
            player_used = result.get("player_name") or result.get("lecteur") or None
        except ImportError as e:
            log.error("franime_extractor.py introuvable : %s", e)
            error = RuntimeError("franime_extractor.py introuvable.")
            download_finish(dl_id, "failed", extractor_type=extractor_used, error_message=str(error))
            raise error from e
        except Exception as e:
            download_finish(
                dl_id,
                "failed",
                extractor_type=extractor_used,
                player_name=player_used,
                error_message=str(e) or type(e).__name__,
            )
            raise
    else:
        # ── 2. yt-dlp standard ───────────────────────────────────────────────────
        try:
            extractor_used = "yt-dlp"
            result = _ydlp_download(
                url,
                output_path=target_output_path,
                download_type=download_type,
                quality=quality,
                video_format=video_format,
                audio_format=audio_format,
                language=resolved_lang,
                write_subtitles=write_subtitles,
                progress_hook=progress_hook,
                adaptive_quality=adaptive_quality,
            )
        except Exception as e:
            try:
                from yt_dlp.utils import UnsupportedError, ExtractorError
                is_unsupported = isinstance(e, (UnsupportedError, ExtractorError))
            except ImportError:
                is_unsupported = False

            if not is_unsupported:
                msg = str(e).lower()
                is_unsupported = any(k in msg for k in ("unsupported url", "no video formats", "unable to extract"))

            if is_unsupported:
                log.warning("[yt-dlp] Non supporté → fallback navigateur générique")
                try:
                    from browser_extractor import download_with_browser
                    extractor_used = "browser"
                    result = download_with_browser(
                        url,
                        output_path=target_output_path,
                        download_type=download_type,
                        quality=quality,
                    )
                    player_used = result.get("stream_url") and "browser_stream" or None
                except ImportError as e2:
                    log.error("browser_extractor.py introuvable : %s", e2)
                    error = RuntimeError("browser_extractor.py introuvable.")
                    download_finish(dl_id, "failed", extractor_type=extractor_used, error_message=str(error))
                    raise error from e2
                except Exception as e2:
                    download_finish(
                        dl_id,
                        "failed",
                        extractor_type=extractor_used,
                        player_name=player_used,
                        error_message=str(e2) or type(e2).__name__,
                    )
                    raise
            else:
                download_finish(
                    dl_id,
                    "failed",
                    extractor_type=extractor_used,
                    player_name=player_used,
                    error_message=str(e) or type(e).__name__,
                )
                raise

    filepath = result.get("filepath")
    if not filepath and result.get("reason") in {"not_found_or_blocked", "page_blocked"}:
        result["skipped"] = True
    if filepath:
        result["relative_path"] = _relative_download_path(filepath, base_output_path)
        result["filename"] = os.path.basename(filepath)
    # Sous-titres sidecar → chemins relatifs servables via /media/
    raw_subs = result.get("subtitle_files") or []
    relative_subs: list[str] = []
    for sub_path in raw_subs:
        if isinstance(sub_path, str) and os.path.isfile(sub_path):
            relative_subs.append(_relative_download_path(sub_path, base_output_path))
    if relative_subs:
        result["subtitle_files"] = relative_subs
        log.info("Sous-titres attaches : %s", ", ".join(os.path.basename(s) for s in relative_subs))
    result["folder"] = os.path.relpath(target_output_path, start=base_output_path).replace("\\", "/")
    if resolved_lang:
        result["language"] = resolved_lang

    # ── Fin tracking BDD ────────────────────────────────────────────────────
    final_status = "success" if filepath else ("skipped" if result.get("skipped") else "failed")
    fsize = None
    dur_ms = None
    metadata: dict[str, Any] = {}
    if filepath and os.path.isfile(filepath):
        try:
            fsize = os.path.getsize(filepath)
        except OSError:
            fsize = None
    # duration was previously taken from info dict — optional now
    if result.get("skipped"):
        metadata["reason"] = result.get("reason")
    if relative_subs:
        metadata["subtitle_files"] = relative_subs
    download_finish(
        dl_id,
        final_status,
        extractor_type=extractor_used,
        player_name=player_used,
        error_message=result.get("error") if final_status != "success" else None,
        file_path=filepath,
        file_size_bytes=fsize,
        duration_ms=dur_ms,
        metadata=metadata,
    )
    if final_status == "success":
        log.success("Téléchargement #%d OK — %s", dl_id, result.get("filename") or filepath or "")
    elif final_status == "skipped":
        log.warning("Téléchargement #%d sauté : %s", dl_id, result.get("reason"))
    else:
        log.error("Téléchargement #%d échoué : %s", dl_id, result.get("error"))
    return result


def _slugify_filename(value: str) -> str:
    import unicodedata
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    name = re.sub(r"[^a-zA-Z0-9\._\- ]+", " ", value).strip()
    name = re.sub(r"\s+", "-", name)
    return name or "media"


def _extract_info_with_cookie_retry(url: str, ydl_opts: dict[str, Any]) -> dict[str, Any]:
    """extract_info avec repli sur les cookies (fichier ou Chrome) si l'accès est bloqué."""
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # pyright: ignore[reportArgumentType]
            return ydl.extract_info(url, download=True)
    except Exception as e:
        if not _needs_session(e):
            raise
        cookie_file = _resolve_cookie_file()
        log.warning(
            "[cookies] Accès restreint ou session requise ; nouvelle tentative avec %s",
            "le fichier youtube_cookies.txt" if cookie_file else "les cookies du navigateur Chrome",
        )
        retry_opts = _cookie_retry_options(ydl_opts, cookie_file)
        with yt_dlp.YoutubeDL(retry_opts) as ydl:  # pyright: ignore[reportArgumentType]
            return ydl.extract_info(url, download=True)


def _ydlp_download(
    url,
    output_path="downloads",
    download_type="video",
    quality="best",
    video_format="mp4",
    audio_format="mp3",
    language: str | None = None,
    write_subtitles: bool = False,
    progress_hook=None,
    adaptive_quality: bool = True,
) -> dict[str, Any]:
    """Téléchargement yt-dlp interne (version étendue)."""
    os.makedirs(output_path, exist_ok=True)

    ffmpeg_dir = _resolve_ffmpeg_dir()
    has_ffmpeg = bool(ffmpeg_dir) or (bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe")))
    host = (urlparse(url).hostname or "").lower()
    is_youtube = host in {"youtube.com", "youtu.be"} or host.endswith(".youtube.com")

    # ── Construction des options yt-dlp (passe unique : vidéo + sous-titres) ──
    subtitle_lang_opts = _build_subtitle_lang_options(language) if write_subtitles else {}
    runtime_options = yt_dlp_runtime_options()
    aria2c_path = shutil.which("aria2c")

    def build_ydl_opts(quality_key: str, force_native: bool = False) -> dict[str, Any]:
        """Options yt-dlp pour une qualité donnée (reconstruites à chaque tentative)."""
        if download_type == "audio":
            format_str = _build_audio_format_string(audio_format)
            format_sort = None
            audio_pp = _build_audio_postprocessor(audio_format, has_ffmpeg)
            postprocessors: list[dict[str, Any]] = [audio_pp] if audio_pp else []
            merge_output_format = None
        else:  # video
            # On préfère toujours les protocoles HTTP (plus stables avec aria2c)
            format_str = _build_video_format_string(
                quality_key, video_format, has_ffmpeg, prefer_http=True
            )
            format_sort = _build_video_format_sort(quality_key)
            postprocessors = []
            f_meta = VIDEO_OUTPUT_FORMATS.get(video_format, VIDEO_OUTPUT_FORMATS["best"])
            container = f_meta["container"]
            merge_output_format = (
                container if (has_ffmpeg and container and f_meta["merge"]) else None
            )

        opts: dict[str, Any] = {
            "format": format_str,
            "outtmpl": os.path.join(output_path, "%(title).200s.%(ext)s").replace("\\", "/"),
            "quiet": True, # On reste discret pour tous les sites
            "no_warnings": False,
            "postprocessors": postprocessors,
            "prefer_ffmpeg": has_ffmpeg,
            "merge_output_format": merge_output_format,
            "noplaylist": True,
            "concurrent_fragment_downloads": 1 if force_native else 16,
            "retries": 10, # 10 tentatives pour tout le monde
            "fragment_retries": 10,
            "file_access_retries": 5,
            "socket_timeout": 30,
            "http_chunk_size": 10485760,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        if format_sort:
            opts["format_sort"] = format_sort
        # Sous-titres récupérés dans la même passe que la vidéo
        if subtitle_lang_opts:
            opts.update(subtitle_lang_opts)
            if has_ffmpeg:
                opts["postprocessors"].append({"key": "FFmpegSubtitlesConvertor", "format": "vtt"})
        
        # Runtime et cookies
        opts.update(runtime_options)
        if ffmpeg_dir:
            opts["ffmpeg_location"] = ffmpeg_dir

        # Stratégie Turbo (aria2c) vs Native
        if aria2c_path and not force_native:
            opts["external_downloader"] = "aria2c"
            opts["external_downloader_args"] = [
                "--max-connection-per-server=16",
                "--split=16",
                "--min-split-size=1M",
                "--max-concurrent-downloads=16",
                "--optimize-concurrent-downloads=true",
                "--file-allocation=none",
                "--disk-cache=64M",
                "--continue=true",
            ]
        return opts

    monitor_adaptive = bool(adaptive_quality and download_type == "video" and quality == "best")
    requested_quality = quality
    current_quality = quality
    quality_changed = False
    adaptive_note: str | None = None

    if aria2c_path:
        log.info("[turbo] Téléchargeur rapide activé (x16 connexions)")
    else:
        log.info("[native] Téléchargeur yt-dlp activé")

    before_files = set(os.listdir(output_path))

    try:
        log.info("yt-dlp — URL: %s", url)
        extra_bits = [f"Type: {download_type.upper()}"]
        extra_bits.append(f"Format: {video_format if download_type == 'video' else audio_format}")
        if download_type == "video":
            extra_bits.append(
                f"Quality: {current_quality.upper()}" + (" (adaptatif 10s)" if monitor_adaptive else "")
            )
        if language:
            extra_bits.append(f"Lang: {language}")
        if write_subtitles:
            extra_bits.append("Sous-titres: oui")
        log.info("yt-dlp — %s", " | ".join(extra_bits))

        # Lancement en qualité max. En mode adaptatif, un watcher mesure le débit
        # réel pendant 10 s et relance à une qualité inférieure si l'ETA est trop
        # longue. En cas de blocage (403/Forbidden), on bascule du mode Turbo
        # (aria2c) vers le mode Natif (discret).
        force_native = False
        while True:
            ydl_opts = build_ydl_opts(current_quality, force_native=force_native)
            hooks: list[Callable[[dict[str, Any]], None]] = []
            if progress_hook is not None:
                hooks.append(progress_hook)
            if monitor_adaptive:
                hooks.append(_make_adaptive_watch_hook())
            if hooks:
                ydl_opts["progress_hooks"] = hooks

            try:
                info = _extract_info_with_cookie_retry(url, ydl_opts)
                break
            except _AdaptiveDowngrade as downgrade:
                log.info("[adaptive] %s", downgrade.note)
                adaptive_note = downgrade.note
                quality_changed = True
                _cleanup_partial_files(output_path, before_files)
                current_quality = downgrade.quality
            except Exception as e:
                # Si on était en mode Turbo et qu'on détecte un blocage/erreur protocole,
                # on tente une dernière chance en mode Natif (discret).
                if not force_native and aria2c_path:
                    msg = str(e).lower()
                    is_blocking = any(k in msg for k in ("403", "forbidden", "inaccessible", "429", "denied"))
                    if is_blocking:
                        log.warning("[fallback] Blocage détecté en mode Turbo -> passage en mode Natif discret")
                        force_native = True
                        _cleanup_partial_files(output_path, before_files)
                        continue
                raise

        raw_title = info.get('title') or "download"
        log.success("yt-dlp — Téléchargé : %s", raw_title)

        created_files = _collect_created_files(output_path, before_files)

        original_file = _resolve_downloaded_file(info, created_files, output_path, download_type)
        if original_file and os.path.isfile(original_file):
            dir_name = os.path.dirname(original_file)
            base_name = os.path.basename(original_file)
            name_part, extension = os.path.splitext(base_name)

            clean_name = _slugify_filename(name_part)
            new_file = os.path.join(dir_name, f"{clean_name}{extension}")

            if original_file != new_file:
                if os.path.exists(new_file):
                    # Collision : au lieu d'écraser silencieusement, ajouter suffixe -numéro
                    collision_counter = 2
                    while True:
                        alt = os.path.join(dir_name, f"{clean_name}-{collision_counter}{extension}")
                        if not os.path.exists(alt):
                            log.warning(
                                "[playlist] Collision détectée avec %s, renommage en %s",
                                os.path.basename(new_file), os.path.basename(alt),
                            )
                            new_file = alt
                            break
                        collision_counter += 1
                os.rename(original_file, new_file)
                selected_file = new_file
            else:
                selected_file = original_file
        else:
            selected_file = original_file

        subtitle_paths = _collect_subtitle_sidecars(selected_file, created_files, output_path)

        return {
            "title": info.get("title") if isinstance(info, dict) else "download",
            "filepath": selected_file,
            "filename": os.path.basename(selected_file) if selected_file else None,
            "subtitle_files": subtitle_paths,
            "quality_requested": requested_quality,
            "quality_used": current_quality,
            "quality_changed": quality_changed,
            "adaptive_note": adaptive_note,
            # Ne PAS renvoyer info yt-dlp (objets non JSON-serialisables)
        }
    except Exception as e:
        cookie_error = _friendly_cookie_error(e)
        if cookie_error:
            log.error("[cookies] %s", cookie_error)
            raise RuntimeError(cookie_error) from e
        if "ffmpeg" in str(e).lower() or "ffprobe" in str(e).lower():
            log.critical("FFmpeg introuvable ou en erreur — %s", e)
            log.error("FFmpeg est requis pour conversion audio / fusion conteneurs.")
            log.error("Install : winget install Gyan.FFmpeg  OU  https://ffmpeg.org/download.html")
        else:
            log.error("Échec yt-dlp : %s", e, exc_info=True)
        raise


# ─── Route /config expose au frontend les listes de formats / langues ────────

def get_frontend_config() -> dict[str, Any]:
    """Payload statique pour initialiser les sélecteurs frontend."""
    return {
        "video_formats": [
            {"key": k, "label": v["label"], "ext": v["ext"]}
            for k, v in VIDEO_OUTPUT_FORMATS.items()
        ],
        "audio_formats": [
            {"key": k, "label": v["label"], "ext": v["ext"]}
            for k, v in AUDIO_OUTPUT_FORMATS.items()
        ],
        "video_qualities": [
            {"key": k, "label": v["label"], "max_height": v["max_height"]}
            for k, v in VIDEO_QUALITIES.items()
        ],
        "languages": [
            {"code": k, "label": v["label"]}
            for k, v in LANGUAGES.items()
        ],
    }


if __name__ == "__main__":
    print("=" * 50)
    print("DowFlow - Multi-Site Media Downloader")
    print("=" * 50)

    video_url = input("Enter URL: ").strip()

    print("\nDownload Options:")
    print("1. Audio (choisir format)")
    print("2. Video (choisir format + qualite)")

    choice = input("\nSelect option (1-2): ").strip()

    if choice == "1":
        print("\nFormats audio disponibles :")
        for i, (k, v) in enumerate(AUDIO_OUTPUT_FORMATS.items(), 1):
            print(f"{i}. {v['label']}")
        try:
            af_choice = int(input("\nChoix format audio : ").strip())
            keys = list(AUDIO_OUTPUT_FORMATS.keys())
            audio_format = keys[af_choice - 1] if 1 <= af_choice <= len(keys) else "mp3"
        except ValueError:
            audio_format = "mp3"
        lang = input("Langue (code ISO ou vide=auto) : ").strip() or None
        download_media(video_url, download_type="audio", audio_format=audio_format, language=lang)
    else:
        print("\nFormats video disponibles :")
        for i, (k, v) in enumerate(VIDEO_OUTPUT_FORMATS.items(), 1):
            print(f"{i}. {v['label']}")
        try:
            vf_choice = int(input("\nChoix format video : ").strip())
            vkeys = list(VIDEO_OUTPUT_FORMATS.keys())
            video_format = vkeys[vf_choice - 1] if 1 <= vf_choice <= len(vkeys) else "mp4"
        except ValueError:
            video_format = "mp4"

        print("\nQualites disponibles :")
        qkeys = list(VIDEO_QUALITIES.keys())
        for i, k in enumerate(qkeys, 1):
            print(f"{i}. {VIDEO_QUALITIES[k]['label']}")
        try:
            q_choice = int(input("\nChoix qualite : ").strip())
            quality = qkeys[q_choice - 1] if 1 <= q_choice <= len(qkeys) else "best"
        except ValueError:
            quality = "best"
        lang = input("Langue (code ISO ou vide=auto) : ").strip() or None
        download_media(video_url, download_type="video", quality=quality,
                       video_format=video_format, language=lang)
