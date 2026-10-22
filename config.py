import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_FOLDER = BASE_DIR / "downloads"

# FFmpeg configuration
FFMPEG_FALLBACK_DIRS = [
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft"
    / "WinGet"
    / "Packages"
    / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    / "ffmpeg-8.1.1-full_build"
    / "bin"
]

# Media settings
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".m4v", ".mov", ".avi", ".flv", ".wmv", ".ts", ".m2ts", ".3gp", ".ogv"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".opus", ".ogg", ".flac", ".wma", ".alac", ".aiff", ".oga"}
ALLOWED_LOCAL_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
PREVIEWABLE_EXTENSIONS = {
    ".mp4", ".webm", ".ogv",         # Vidéo HTML5 native
    ".mp3", ".m4a", ".aac",         # Audio universel
    ".ogg", ".opus", ".wav", ".flac", # Audio Firefox/Chrome
}

# ── Formats de sortie Vidéo ──────────────────────────────────────────────────
#   key → {"label", "ext", "container", "merge": bool,
#          "aext":  extention audio pour yt-dlp [ext=...]  (ex: "m4a", "opus", None=auto)
#          "vext":  extention vidéo pour yt-dlp [ext=...]  (ex: "mp4", "webm", None=auto)
#          Anciens noms "vcodec"/"acodec" conservés 1 release pour rétro-compatibilité.
VIDEO_OUTPUT_FORMATS: dict = {
    "best":   {"label": "Auto (meilleur possible)", "ext": None,  "container": None,   "merge": True,  "aext": None, "vext": None, "acodec": None, "vcodec": None},
    "mp4":    {"label": "MP4 (universel)",          "ext": ".mp4", "container": "mp4",  "merge": True,  "aext": "m4a","vext": "mp4","acodec": "m4a","vcodec": "mp4"},
    "mkv":    {"label": "MKV (matroska)",           "ext": ".mkv", "container": "mkv",  "merge": True,  "aext": None, "vext": None, "acodec": None, "vcodec": None},
    "webm":   {"label": "WebM (libre)",             "ext": ".webm","container": "webm", "merge": True,  "aext": "opus","vext":"webm","acodec": "opus","vcodec": "webm"},
    "mov":    {"label": "MOV (QuickTime)",          "ext": ".mov", "container": "mov",  "merge": True,  "aext": None, "vext": None, "acodec": None, "vcodec": None},
    "avi":    {"label": "AVI (legacy)",             "ext": ".avi", "container": "avi",  "merge": False, "aext": None, "vext": None, "acodec": None, "vcodec": None},
}

# ── Formats de sortie Audio ──────────────────────────────────────────────────
#   key → {"label", "ext", "codec", "quality": str|int (bitrate kbps ou 0=best)}
AUDIO_OUTPUT_FORMATS: dict = {
    "best":  {"label": "Auto (meilleur possible)", "ext": None,   "codec": None,  "quality": 0},
    "mp3":   {"label": "MP3 (192 kbps)",           "ext": ".mp3", "codec": "mp3", "quality": 192},
    "mp3_320":{"label": "MP3 (320 kbps HQ)",       "ext": ".mp3", "codec": "mp3", "quality": 320},
    "flac":  {"label": "FLAC (lossless)",          "ext": ".flac","codec": "flac","quality": 0},
    "wav":   {"label": "WAV (non compressé)",      "ext": ".wav", "codec": "wav", "quality": 0},
    "aac":   {"label": "AAC (256 kbps)",           "ext": ".m4a", "codec": "aac", "quality": 256},
    "m4a":   {"label": "M4A (iTunes)",             "ext": ".m4a", "codec": "m4a", "quality": 192},
    "opus":  {"label": "Opus (160 kbps)",          "ext": ".opus","codec": "opus","quality": 160},
    "ogg":   {"label": "OGG Vorbis (192 kbps)",    "ext": ".ogg", "codec": "vorbis","quality": 192},
    "alac":  {"label": "ALAC (lossless Apple)",    "ext": ".m4a", "codec": "alac","quality": 0},
}

# ── Qualités vidéo ───────────────────────────────────────────────────────────
#   key → {"label", "max_height": int|None, "alias": str}
VIDEO_QUALITIES: dict = {
    "best":   {"label": "Auto max",        "max_height": None, "alias": "best"},
    "4320p":  {"label": "8K (4320p)",      "max_height": 4320, "alias": "8k"},
    "2160p":  {"label": "4K UHD (2160p)",  "max_height": 2160, "alias": "ultra"},
    "1440p":  {"label": "QHD (1440p)",     "max_height": 1440, "alias": "qhd"},
    "1080p":  {"label": "Full HD (1080p)", "max_height": 1080, "alias": "high"},
    "720p":   {"label": "HD (720p)",       "max_height": 720,  "alias": "medium"},
    "480p":   {"label": "SD (480p)",       "max_height": 480,  "alias": "low"},
    "360p":   {"label": "360p",            "max_height": 360,  "alias": "360"},
    "240p":   {"label": "240p",            "max_height": 240,  "alias": "240"},
}

# ── Langues supportées ───────────────────────────────────────────────────────
#   {code_iso_639_1: {"label": str, "aliases": set[str]}}
LANGUAGES: dict = {
    # ── Franime / Francophone ────────────────────────────────────────────
    "vf":     {"label": "VF (Français doublé)",      "aliases": {"vf", "fr-dub", "french-dub"}},
    "vo":     {"label": "VOSTFR (VOST Français)",    "aliases": {"vo", "vostfr", "vost", "sub", "subs", "fr-sub", "vof"}},
    "fr":     {"label": "Français",                  "aliases": {"fr", "fra", "fre", "french"}},
    # ── Mondiales ────────────────────────────────────────────────────────
    "en":     {"label": "English",                   "aliases": {"en", "eng", "english"}},
    "es":     {"label": "Español / Spanish",         "aliases": {"es", "spa", "spanish", "castellano"}},
    "de":     {"label": "Deutsch",                   "aliases": {"de", "deu", "ger", "german"}},
    "it":     {"label": "Italiano",                  "aliases": {"it", "ita", "italian"}},
    "pt":     {"label": "Português",                 "aliases": {"pt", "por", "portuguese"}},
    "pt-br":  {"label": "Português (BR)",            "aliases": {"pt-br", "pob", "brazilian"}},
    "ru":     {"label": "Русский / Russian",         "aliases": {"ru", "rus", "russian"}},
    "ja":     {"label": "日本語 / Japanese",         "aliases": {"ja", "jpn", "japanese", "jp"}},
    "ko":     {"label": "한국어 / Korean",           "aliases": {"ko", "kor", "korean"}},
    "zh":     {"label": "中文 / Chinese",            "aliases": {"zh", "chi", "chinese", "cmn", "zh-cn", "zh-tw", "zh-hans", "zh-hant", "zh_cn", "zh_tw"}},
    "ar":     {"label": "العربية / Arabic",          "aliases": {"ar", "ara", "arabic"}},
    "hi":     {"label": "हिन्दी / Hindi",            "aliases": {"hi", "hin", "hindi"}},
    "tr":     {"label": "Türkçe / Turkish",          "aliases": {"tr", "tur", "turkish"}},
    "pl":     {"label": "Polski / Polish",           "aliases": {"pl", "pol", "polish"}},
    "nl":     {"label": "Nederlands / Dutch",        "aliases": {"nl", "nld", "dut", "dutch"}},
    "sv":     {"label": "Svenska / Swedish",         "aliases": {"sv", "swe", "swedish"}},
    "no":     {"label": "Norsk / Norwegian",         "aliases": {"no", "nor", "norwegian"}},
    "da":     {"label": "Dansk / Danish",            "aliases": {"da", "dan", "danish"}},
    "fi":     {"label": "Suomi / Finnish",           "aliases": {"fi", "fin", "finnish"}},
    "cs":     {"label": "Čeština / Czech",           "aliases": {"cs", "ces", "cze", "czech"}},
    "hu":     {"label": "Magyar / Hungarian",        "aliases": {"hu", "hun", "hungarian"}},
    "ro":     {"label": "Română / Romanian",         "aliases": {"ro", "ron", "rum", "romanian"}},
    "bg":     {"label": "Български / Bulgarian",     "aliases": {"bg", "bul", "bulgarian"}},
    "el":     {"label": "Ελληνικά / Greek",          "aliases": {"el", "ell", "gre", "greek"}},
    "he":     {"label": "עברית / Hebrew",            "aliases": {"he", "heb", "hebrew"}},
    "th":     {"label": "ไทย / Thai",                "aliases": {"th", "tha", "thai"}},
    "vi":     {"label": "Tiếng Việt / Vietnamese",   "aliases": {"vi", "vie", "vietnamese"}},
    "id":     {"label": "Indonesia / Indonesian",    "aliases": {"id", "ind", "indonesian"}},
    "ms":     {"label": "Melayu / Malay",            "aliases": {"ms", "msa", "may", "malay"}},
    "uk":     {"label": "Українська / Ukrainian",    "aliases": {"uk", "ukr", "ukrainian"}},
    "ca":     {"label": "Català / Catalan",          "aliases": {"ca", "cat", "catalan"}},
    "eu":     {"label": "Euskara / Basque",          "aliases": {"eu", "eus", "basque"}},
    "fa":     {"label": "فارسی / Persian",           "aliases": {"fa", "fas", "per", "persian"}},
    "ta":     {"label": "தமிழ் / Tamil",             "aliases": {"ta", "tam", "tamil"}},
    "te":     {"label": "తెలుగు / Telugu",           "aliases": {"te", "tel", "telugu"}},
    "bn":     {"label": "বাংলা / Bengali",           "aliases": {"bn", "ben", "bengali"}},
    "ur":     {"label": "اردو / Urdu",               "aliases": {"ur", "urd", "urdu"}},
    "multi":  {"label": "Multi-langues / toutes",    "aliases": {"multi", "all", "multilingual"}},
}


def normalize_language(lang_value: str | None) -> str:
    """Normalise toute entrée de langue vers son code ISO canonique."""
    raw = (lang_value or "").strip().lower()
    if not raw:
        return ""
    if raw in LANGUAGES:
        return raw
    for key, meta in LANGUAGES.items():
        if raw in meta["aliases"]:
            return key
    return raw


def detect_language_from_url(url: str) -> str:
    """Tente de détecter une langue à partir d'une URL (paramètres, chemin, domaine)."""
    from urllib.parse import parse_qs, urlparse
    try:
        parsed = urlparse(url)
    except Exception:
        return ""

    # 1) Paramètre query : ?lang= ?hl= ?language= ?sub_lang= ?audio_language=
    for param in ("lang", "hl", "language", "sub_lang", "sub_language", "audio_language", "alang", "slang"):
        values = parse_qs(parsed.query).get(param)
        if values and values[0]:
            detected = normalize_language(values[0])
            if detected:
                return detected

    # 2) Chemin : /fr/ /en/ /vostfr/ /vf/ …
    path_lower = parsed.path.lower()
    for key, meta in LANGUAGES.items():
        candidates = {key, *meta["aliases"]}
        for token in candidates:
            if len(token) < 2:
                continue
            if f"/{token}/" in path_lower or path_lower.endswith(f"/{token}"):
                return key

    # 3) Sous-domaine / TLD : fr.youtube.com, example.es…
    host = (parsed.netloc or "").lower()
    for key, meta in LANGUAGES.items():
        candidates = {key, *meta["aliases"]}
        for token in candidates:
            if len(token) != 2:
                continue
            if host.startswith(f"{token}.") or host.endswith(f".{token}"):
                return key

    return ""


# App settings
LOCAL_MEDIA_TTL_SECONDS = 3600
LOCAL_MEDIA_MAX_ENTRIES = 128

# Browser extractor settings
# Set DOWFLOW_BROWSER_VISIBLE=1 before starting the app if you need to debug the browser.
BROWSER_HEADLESS = os.environ.get("DOWFLOW_BROWSER_VISIBLE", "").strip().lower() not in {"1", "true", "yes", "on"}
BROWSER_PROFILE_DIR = BASE_DIR / "browser_profile"
YOUTUBE_COOKIES_FILE = BASE_DIR / "youtube_cookies.txt"

# Proxy settings (Optional)
# Format: ["http://user:pass@host:port", "http://host2:port2"]
# Leave empty [] to not use proxies.
# Note: free public proxies often time out; Direct is tried first.
PROXIES = []

# Performance settings
MAX_DOWNLOADS_BEFORE_REFRESH = 10 # Nombre de téléchargements avant de forcer un nettoyage console/mémoire
