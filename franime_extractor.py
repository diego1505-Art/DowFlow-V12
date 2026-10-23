"""
Extracteur franime.fr — supporte SIBNET, FILEMOON, SENDVID, VIDMOLY et HLS direct.
Lance Chrome en arrière-plan par défaut, intercepte les URLs des lecteurs, essaie chaque lecteur dans l'ordre.
"""
from __future__ import annotations
import re, os, shutil, tempfile, time, random
from pathlib import Path
from typing import Any

from config import BROWSER_HEADLESS, BROWSER_PROFILE_DIR, PROXIES
from logger import get_logger
from utils import _resolve_ffmpeg_dir, find_chrome_binary, has_ffmpeg, yt_dlp_runtime_options

log = get_logger("franime_ext")

# ── Anti-Bot Settings ────────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

def human_delay(min_ms=500, max_ms=1500):
    """Pause aléatoire pour simuler un comportement humain."""
    time.sleep(random.randint(min_ms, max_ms) / 1000.0)

def move_mouse_humanly(page):
    """Déplace la souris de manière moins linéaire."""
    try:
        width, height = 1280, 720
        for _ in range(random.randint(2, 5)):
            x, y = random.randint(0, width), random.randint(0, height)
            page.mouse.move(x, y, steps=random.randint(5, 15))
            human_delay(100, 300)
    except Exception:
        pass

# ── Lecteurs connus et leurs patterns d'URL ──────────────────────────────────
# Chaque entrée : (nom, regex de détection, blacklist regex)
LECTEURS = [
    ("SIBNET",   re.compile(r"sibnet\.ru/shell\.php",          re.I), re.compile(r"sibnet\.ru/export/|sbcount|/time", re.I)),
    ("FILEMOON", re.compile(r"filemoon\.\w+/",                 re.I), None),
    ("SENDVID",  re.compile(r"sendvid\.com/",                  re.I), None),
    ("VIDMOLY",  re.compile(r"vidmoly\.\w+/",                  re.I), None),
    ("HLS",      re.compile(r"\.(m3u8|mpd)(\?|$)",            re.I), None),
    ("WATCH2",   re.compile(r"franime\.fr/watch2/",            re.I), None),
]

SKIP_HOSTS = {
    "google-analytics", "googletagmanager", "doubleclick", "facebook",
    "twitter", "sentry", "fonts.google", "gstatic", "clarity.ms",
    "hotjar", "segment.io", "amplitude", "adskeeper", "bidgear",
    "a-ads.com", "betweendigital", "yandex", "mail.ru",
}

# Priorité de téléchargement (yt-dlp supporte tous ces domaines)
LECTEUR_PRIORITY = ["HLS", "SIBNET", "SENDVID", "VIDMOLY", "FILEMOON", "WATCH2"]


def _skip(url: str) -> bool:
    return url.startswith("blob:") or any(h in url for h in SKIP_HOSTS)


def _detect_lecteur(url: str) -> str | None:
    """Retourne le nom du lecteur détecté pour une URL donnée."""
    for name, pattern, blacklist in LECTEURS:
        if blacklist and blacklist.search(url):
            continue
        if pattern.search(url):
            return name
    return None


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
        for known_ext in (".mp4", ".webm", ".mkv", ".m4v", ".mov", ".avi", ".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus"):
            candidates.append(os.path.join(output_path, f"{title}{known_ext}"))

    unique_candidates: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        normalized = os.path.normpath(path)
        if normalized not in seen:
            unique_candidates.append(normalized)
            seen.add(normalized)

    return unique_candidates


def _resolve_downloaded_file(info: dict[str, Any], output_path: str, before_mtime: dict[str, float]) -> str | None:
    ignored = {".part", ".ytdl", ".jpg", ".jpeg", ".png", ".webp", ".description"}

    for candidate in _candidate_filepaths_from_info(info, output_path):
        if os.path.isfile(candidate) and os.path.splitext(candidate)[1].lower() not in ignored:
            return candidate

    candidates = []
    for f in os.listdir(output_path):
        fp = os.path.join(output_path, f)
        mt = os.path.getmtime(fp)
        if f not in before_mtime or mt > before_mtime[f]:
            if os.path.splitext(f)[1].lower() not in ignored:
                candidates.append((mt, fp))

    candidates.sort(reverse=True)
    return candidates[0][1] if candidates else None


def extract_stream_url(page_url: str, preferred_lecteur: str | None = None, proxy_url: str | None = None) -> tuple[str | None, str | None, dict[str, list[str]], dict[str, Any]]:
    """
    Retourne (url_stream, nom_lecteur, all_captured, diagnostics).
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    except ImportError:
        raise RuntimeError("Playwright non installé. Lance : pip install playwright && playwright install chromium")

    # dict lecteur → liste d'URLs trouvées
    captured: dict[str, list[str]] = {name: [] for name, *_ in LECTEURS}
    url_status: dict[str, int] = {}
    diagnostics: dict[str, Any] = {
        "main_status": None,
        "blocked": False,
        "blocked_url": None,
        "url_status": url_status,
    }

    if proxy_url:
        log.info("  [franime] Utilisation du proxy : %s", proxy_url.split("@")[-1])

    def _remember(name: str, url: str) -> None:
        if url not in captured[name]:
            log.success('  [franime] ✓ [%s] %s', name, url[:110])
            captured[name].append(url)

    def on_request(req):
        url = req.url
        if _skip(url): return
        name = _detect_lecteur(url)
        if name:
            _remember(name, url)

    def on_response(resp):
        url = resp.url
        if _skip(url): return
        # On ne bloque plus sur le 403 immédiatement, Cloudflare renvoie souvent 403/503 pendant le challenge
        if url.split("#", 1)[0] == page_url.split("#", 1)[0]:
            diagnostics["main_status"] = resp.status
            if resp.status in {401, 429}: # On garde 401 et 429 comme bloquants réels
                diagnostics["blocked"] = True
                diagnostics["blocked_url"] = url
        name = _detect_lecteur(url)
        if name:
            url_status[url] = resp.status
            _remember(name, url)
        ct = resp.headers.get("content-type", "").lower()
        if any(t in ct for t in ["mpegurl", "dash+xml", "mp2t", "x-mpegurl"]):
            hls_name = _detect_lecteur(url) or "HLS"
            url_status[url] = resp.status
            _remember(hls_name, url)
        if not any(e in url for e in [".js",".css",".png",".jpg",".svg",".woff",".ico",".gif",".webp",".wasm"]):
            log.info('  [franime]   %s  %s', resp.status, url[:100])

    chrome_path   = _find_chrome()
    user_data_dir = _chrome_user_data_dir()
    
    # Utilisation du profil persistant du projet au lieu d'un temporaire
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    profile_path = str(BROWSER_PROFILE_DIR)

    log.info('  [franime] Chrome : %s', chrome_path or 'Chromium Playwright')
    log.info('  [franime] Profil : %s', profile_path)

    with sync_playwright() as pw:
        # Arguments pour plus de discrétion (anti-bot)
        base_args = [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-position=0,0",
        ]

        # User-Agent aléatoire
        user_agent = random.choice(USER_AGENTS)
        # Viewport aléatoire
        viewport_w = random.randint(1200, 1600)
        viewport_h = random.randint(700, 1000)

        # Proxy config for Playwright
        proxy_config = None
        if proxy_url:
            proxy_config = {"server": proxy_url}

        # Si Chrome est installé, on essaie de synchroniser les cookies initiaux une fois
        if chrome_path and user_data_dir and not (BROWSER_PROFILE_DIR / "Default").exists():
            log.info('  [franime] Synchronisation initiale du profil Chrome...')
            src_default = os.path.join(user_data_dir, "Default")
            dst_default = BROWSER_PROFILE_DIR / "Default"
            dst_default.mkdir(parents=True, exist_ok=True)
            for fname in ["Cookies", "Login Data", "Local State", "Preferences", "Web Data"]:
                src = os.path.join(src_default, fname)
                if os.path.isfile(src):
                    try: shutil.copy2(src, dst_default / fname)
                    except Exception: pass
        
        # Lancement avec contexte PERSISTANT (maintient les sessions/cookies Franime)
        ctx = pw.chromium.launch_persistent_context(
            profile_path, 
            headless=BROWSER_HEADLESS, 
            executable_path=chrome_path,
            args=base_args, 
            no_viewport=False, 
            viewport={"width": viewport_w, "height": viewport_h},
            user_agent=user_agent,
            locale="fr-FR",
            timezone_id="Europe/Paris",
            proxy=proxy_config
        )
        page = ctx.new_page()

        # Injection de script pour masquer Playwright et simuler des caractéristiques réelles
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['fr-FR', 'fr', 'en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            window.chrome = { runtime: {} };
        """)

        # --- PROTECTION LOCALE ---
        # Petite pause aléatoire avant de charger la page
        human_delay(2000, 6000)

        page.on("request",  on_request)
        page.on("response", on_response)

        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=45_000)
        except PwTimeout:
            log.warning('  [franime] Timeout – on continue')

        # Détection Cloudflare
        content_lower = page.content().lower()
        if "challenge-platform" in content_lower or "cloudflare" in content_lower or "just a moment" in page.title().lower():
            log.info("  [franime] Challenge Cloudflare détecté, attente de résolution (jusqu'à 45s)...")
            human_delay(4000, 6000)
            try:
                # On essaie de cliquer sur la checkbox si elle apparaît (iframe Cloudflare)
                for i in range(15): # Augmenté à 15 tentatives
                    curr_content = page.content().lower()
                    if "challenge-platform" not in curr_content and "cloudflare" not in curr_content and "just a moment" not in page.title().lower():
                        break
                    
                    # On bouge la souris de manière aléatoire pour paraître humain
                    move_mouse_humanly(page)
                    
                    # Petit scroll aléatoire
                    if random.random() > 0.5:
                        page.mouse.wheel(0, random.randint(100, 400))
                    
                    # On cherche l'iframe du challenge
                    frames = page.frames
                    for f in frames:
                        if "cloudflare" in f.url or "turnstile" in f.url:
                            try:
                                checkbox = f.query_selector("input[type='checkbox'], #challenge-stage, .ctp-checkbox-container")
                                if checkbox:
                                    # On bouge d'abord sur l'élément avant de cliquer
                                    box = checkbox.bounding_box()
                                    if box:
                                        page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2, steps=10)
                                        human_delay(200, 500)
                                    checkbox.click()
                                    log.success("  [franime] ✓ Tentative de clic Cloudflare (%s)", i + 1)
                            except Exception: pass
                    human_delay(2000, 3500)

                # On attend le bouton final (on gère les traductions possibles)
                page.wait_for_selector("button:has-text('Regarder'), button:has-text('Watch'), .player-container", timeout=35_000)
                log.success('  [franime] ✓ Page accessible !')
            except Exception as e:
                log.error('  [franime] Le challenge semble toujours présent ou erreur : %s', e)
                page.wait_for_timeout(2_000)

        # Si un lecteur préféré est demandé, le sélectionner dans le dropdown
        if preferred_lecteur:
            _select_lecteur(page, preferred_lecteur)

        # Clic "Regarder l'épisode"
        page.wait_for_timeout(2_000)
        selectors = [
            "button:has-text('Regarder')", "a:has-text('Regarder')", 
            "button:has-text('Watch')", "a:has-text('Watch')",
            "button:has-text('épisode')", "button:has-text('episode')"
        ]
        for sel in selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click(timeout=3_000)
                    log.info("  [franime] Clic 'Regarder' : %s", sel)
                    page.wait_for_timeout(2_000)
                    break
            except Exception:
                pass

        # Attendre lecteur
        for sel in ["video", "iframe", "[class*='player']"]:
            try:
                page.wait_for_selector(sel, timeout=10_000)
                break
            except Exception:
                pass

        # Clic play
        for sel in ["video", ".play-button", "[class*='play']"]:
            try:
                el = page.query_selector(sel)
                if el:
                    el.click(timeout=2_000)
                    break
            except Exception:
                pass

        # Attendre jusqu'à 35 secondes
        for i in range(35):
            page.wait_for_timeout(1_000)
            total = sum(len(v) for v in captured.values())
            if total >= 1 and i >= 3:
                break

        # Sendvid souvent en 502 : ouvrir watch2 pour capturer le vrai flux (mp4/m3u8)
        healthy_players = any(
            url_status.get(u, 0) and url_status.get(u, 999) < 400
            for name in ("HLS", "SIBNET", "VIDMOLY", "FILEMOON")
            for u in captured.get(name, [])
        )
        sendvid_broken = captured.get("SENDVID") and all(
            url_status.get(u, 0) >= 400 for u in captured["SENDVID"]
        )
        if captured.get("WATCH2") and (sendvid_broken or not healthy_players):
            watch2_url = captured["WATCH2"][0]
            log.error('  [franime] Sendvid HS / pas de flux direct → ouverture Watch2...')
            try:
                page.goto(watch2_url, wait_until="domcontentloaded", timeout=45_000)
                for sel in ["video", "iframe", "source"]:
                    try:
                        page.wait_for_selector(sel, timeout=8_000)
                        break
                    except Exception:
                        pass
                for i in range(20):
                    page.wait_for_timeout(1_000)
                    if captured.get("HLS") or any(
                        captured.get(n) for n in ("SIBNET", "VIDMOLY", "FILEMOON", "SENDVID")
                        if n != "WATCH2"
                    ):
                        # Nouveau flux utile capturé
                        if any(
                            url_status.get(u, 200) < 400
                            for n in ("HLS", "SIBNET", "VIDMOLY", "FILEMOON", "SENDVID")
                            for u in captured.get(n, [])
                        ):
                            break
                    # Chercher src vidéo dans la page / iframes
                    try:
                        for frame in page.frames:
                            srcs = frame.eval_on_selector_all(
                                "video, video source, source[src], iframe[src]",
                                "els => els.map(e => e.src || e.getAttribute('src')).filter(Boolean)",
                            )
                            for src in srcs or []:
                                name = _detect_lecteur(src) or ("HLS" if ".m3u8" in src or ".mpd" in src else None)
                                if name:
                                    url_status.setdefault(src, 200)
                                    _remember(name, src)
                    except Exception:
                        pass
            except Exception as e:
                log.warning('  [franime] Watch2 non exploitable : %s', e)

        ctx.close()

    # Choisir le meilleur lecteur disponible (ignorer les URLs en 4xx/5xx)
    order = ([preferred_lecteur] if preferred_lecteur else []) + LECTEUR_PRIORITY
    for name in order:
        urls = captured.get(name, [])
        healthy = [u for u in urls if url_status.get(u, 200) < 400]
        pick = healthy[0] if healthy else (urls[0] if urls and name == "WATCH2" else None)
        if pick:
            status = url_status.get(pick)
            status_txt = f" (HTTP {status})" if status is not None else ""
            log.success('  [franime] Lecteur choisi : %s → %s%s', name, pick[:100], status_txt)
            return pick, name, captured, diagnostics

    return None, None, captured, diagnostics


def _select_lecteur(page, lecteur_name: str):
    """Essaie de sélectionner un lecteur spécifique dans le dropdown franime."""
    try:
        # Ouvrir le dropdown
        for sel in ["select", "[class*='lecteur']", "[class*='player-select']", "button:has-text('Lecteur')"]:
            el = page.query_selector(sel)
            if el:
                el.click(timeout=2_000)
                page.wait_for_timeout(500)
                # Chercher l'option du lecteur voulu
                opt = page.query_selector(f"option:has-text('{lecteur_name}'), li:has-text('{lecteur_name}')")
                if opt:
                    opt.click(timeout=2_000)
                    log.success('  [franime] Lecteur sélectionné : %s', lecteur_name)
                    page.wait_for_timeout(1_500)
                    return
    except Exception as e:
        log.error('  [franime] Impossible de changer de lecteur : %s', e)


def download_franime(url: str, output_path: str = "downloads",
                     download_type: str = "video", quality: str = "best",
                     video_format: str = "mp4", audio_format: str = "mp3",
                     language: str | None = None) -> dict[str, Any]:
    os.makedirs(output_path, exist_ok=True)

    # --- ANTI-SPAM DELAY ---
    log.info('  [franime] Pause de sécurité anti-spam...')
    human_delay(3000, 8000)

    # Direct d'abord : les proxies publics timeout souvent et font perdre plusieurs minutes.
    shuffled_proxies = list(PROXIES)
    random.shuffle(shuffled_proxies)
    proxies_to_try = [None] + shuffled_proxies

    # 1. Tentative d'extraction avec rotation des proxies
    stream_url = None
    nom = None
    all_captured: dict[str, list[str]] = {}
    url_status: dict[str, int] = {}
    
    for proxy in proxies_to_try:
        try:
            log.info("\n  [franime] Analyse de la page (Proxy: %s) : %s", proxy or "Direct", url)
            stream_url, nom, all_captured, diagnostics = extract_stream_url(url, proxy_url=proxy)
            url_status = diagnostics.get("url_status") or {}
            
            if stream_url:
                break # On a trouvé un flux !
                
            if diagnostics.get("blocked") and diagnostics.get("main_status") in {401, 429}:
                log.error(
                    "  [franime] Proxy bloqué ou erreur fatale (%s). Suivant...",
                    diagnostics.get("main_status"),
                )
                continue
                
        except Exception as e:
            log.error('  [franime] ✗ Erreur avec le proxy %s : %s', proxy, e)
            continue

    if not stream_url and not all_captured:
        return {
            "success": False,
            "error": "Impossible d'extraire les flux, même après rotation des proxies.",
            "reason": "page_blocked"
        }

    # On garde trace des URLs déjà testées pour ne pas boucler inutilement
    tried_urls = set()

    def try_download(s_url, s_name):
        if not s_url or s_url in tried_urls:
            return None
        # Ne pas tenter un embed déjà vu en 4xx/5xx pendant la capture
        status = url_status.get(s_url)
        if status is not None and status >= 400 and s_name != "WATCH2":
            log.warning('  [franime] Skip [%s] HTTP %s : %s', s_name, status, s_url[:100])
            tried_urls.add(s_url)
            return None
        tried_urls.add(s_url)
        
        for proxy in proxies_to_try:
            log.info(
                "  [franime] Tentative [%s] (Proxy: %s) : %s...",
                s_name,
                proxy or "Direct",
                s_url[:100],
            )
            try:
                res = _download_stream(s_url, s_name, url, output_path, download_type, quality,
                                       video_format=video_format, audio_format=audio_format,
                                       language=language, proxy_url=proxy)
                if res.get("filename"):
                    return res
            except Exception as e:
                log.error('  [franime] ✗ Échec proxy %s : %s', proxy, e)
                continue
        return None

    # 2. On essaie d'abord le "meilleur" trouvé lors du premier passage
    if stream_url:
        result = try_download(stream_url, nom)
        if result: return result

    # 3. On essaie toutes les autres URLs capturées, par ordre de priorité
    for name in LECTEUR_PRIORITY:
        for s_url in all_captured.get(name, []):
            result = try_download(s_url, name)
            if result: return result

    # 4. Forcer un autre lecteur dans le navigateur si rien n'a marché
    for lecteur in LECTEUR_PRIORITY:
        if all_captured.get(lecteur):
            continue
        
        log.info('\\n  [franime] Forçage du lecteur : %s', lecteur)
        try:
            stream_url, nom, captured_now, diagnostics_now = extract_stream_url(url, preferred_lecteur=lecteur)
            url_status.update(diagnostics_now.get("url_status") or {})
            if diagnostics_now.get("blocked") and not stream_url and diagnostics_now.get("main_status") != 403:
                status = diagnostics_now.get("main_status")
                log.error("  [franime] Bloqué (%s).", status)
                continue
            if stream_url:
                result = try_download(stream_url, nom)
                if result: return result
            
            for name in LECTEUR_PRIORITY:
                for s_url in captured_now.get(name, []):
                    result = try_download(s_url, name)
                    if result: return result
        except Exception as e:
            log.error('  [franime] Erreur lors du forçage %s : %s', lecteur, e)

    log.warning("  [franime] Aucun lecteur n'a fonctionné pour %s", url)
    return {
        "success": False,
        "filename": None,
        "filepath": None,
        "error": "Lecteur non capturé ou indisponible sur Franime.",
        "reason": "not_found_or_blocked"
    }


def _download_stream(stream_url: str, lecteur_name: str, page_url: str,
                     output_path: str, download_type: str, quality: str,
                     video_format: str = "mp4", audio_format: str = "mp3",
                     language: str | None = None,
                     proxy_url: str | None = None) -> dict[str, Any]:
    import yt_dlp
    import subprocess

    is_sibnet  = "sibnet.ru"   in stream_url
    is_filemoon= "filemoon"    in stream_url
    referer    = (
        "https://video.sibnet.ru/" if is_sibnet else
        "https://filemoon.sx/"     if is_filemoon else
        "https://franime.fr/"
    )

    slug   = re.search(r"/anime/([^?/]+)", page_url)
    title  = slug.group(1) if slug else "franime-video"
    params = dict(p.split("=",1) for p in page_url.split("?",1)[-1].split("&") if "=" in p)
    ep     = f"s{params.get('s','1')}-ep{params.get('ep','1')}-{params.get('lang','vo')}"

    ffmpeg_dir = _resolve_ffmpeg_dir()
    ffmpeg_available = has_ffmpeg()
    ffmpeg_exe = "ffmpeg"
    ffprobe_exe = "ffprobe"
    if ffmpeg_dir:
        ffmpeg_exe = os.path.join(ffmpeg_dir, "ffmpeg.exe")
        ffprobe_exe = os.path.join(ffmpeg_dir, "ffprobe.exe")

    ydl_opts: dict[str, Any] = {
        "quiet": False,
        "no_warnings": True,
        "noplaylist": True,
        "concurrent_fragment_downloads": 16,
        "retries": 10,
        "fragment_retries": 10,
        "proxy": proxy_url,
        "http_headers": {
            "Referer":    referer,
            "Origin":     referer.rstrip("/"),
            "User-Agent": random.choice(USER_AGENTS),
        },
    }

    # ── Formats et post-traitements via playlist helpers ────────────────────
    try:
        from playlist import (
            _build_video_format_string,
            _build_video_format_sort,
            _build_audio_format_string,
            _build_audio_postprocessor,
            _build_subtitle_lang_options,
            _resolve_quality_key,
        )
        from config import VIDEO_OUTPUT_FORMATS, AUDIO_OUTPUT_FORMATS
    except Exception:
        _build_video_format_string = None  # type: ignore

    ffmpeg_dir = _resolve_ffmpeg_dir_proxy()
    has_ffmpeg = bool(ffmpeg_dir) or (bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe")))
    ydl_opts.update(yt_dlp_runtime_options())
    if ffmpeg_dir:
        ydl_opts["ffmpeg_location"] = ffmpeg_dir

    if download_type == "audio":
        if _build_video_format_string is not None:
            ydl_opts["format"] = _build_audio_format_string(audio_format)
            pp = _build_audio_postprocessor(audio_format, has_ffmpeg)
            if pp:
                ydl_opts["postprocessors"] = [pp]
        else:
            ydl_opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
            if has_ffmpeg:
                ydl_opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }]
    else:
        if _build_video_format_string is not None:
            ydl_opts["format"] = _build_video_format_string(quality, video_format, has_ffmpeg)
            quality_sort = _build_video_format_sort(quality)
            if quality_sort:
                ydl_opts["format_sort"] = quality_sort
            f_meta = VIDEO_OUTPUT_FORMATS.get(video_format, VIDEO_OUTPUT_FORMATS["best"])
            container = f_meta["container"]
            if has_ffmpeg and container and f_meta["merge"]:
                ydl_opts["merge_output_format"] = container
        else:
            ydl_opts["format"] = "bestvideo+bestaudio/best"
            ydl_opts["merge_output_format"] = "mp4"

    # ── Sous-titres / langue ─────────────────────────────────────────────────
    if language and _build_video_format_string is not None:
        ydl_opts.update(_build_subtitle_lang_options(language))

    # Nom de fichier : on garde le pattern éprouvé slug-s1-ep1-lang.ext
    lang_tag = ""
    if language:
        lang_tag = f"-{language}"
    elif params.get("lang"):
        lang_tag = f"-{params.get('lang')}"
    ep_tag = f"s{params.get('s','1')}-ep{params.get('ep','1')}{lang_tag or ''}"
    ydl_opts["outtmpl"] = os.path.join(output_path, f"{title}-{ep_tag}.%(ext)s")

    if shutil.which("aria2c"):
        ydl_opts["external_downloader"] = "aria2c"
        ydl_opts["external_downloader_args"] = [
            "--max-connection-per-server=16",
            "--split=16",
            "--min-split-size=1M",
            "--continue=true",
        ]
        log.success('  [aria2c] Téléchargeur rapide activé pour Franime')

    # Tentative de téléchargement
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(stream_url, download=True)
            filepath = _resolve_downloaded_file(info, output_path, {})
            
            if filepath and os.path.isfile(filepath):
                if not ffmpeg_available:
                    # Sans FFmpeg on ne peut ni sonder ni réparer : on valide le fichier tel quel.
                    log.warning("  [franime] FFmpeg introuvable : vérification d'intégrité ignorée.")
                    return {
                        "success": True,
                        "filename": os.path.basename(filepath),
                        "filepath": filepath,
                        "folder": os.path.basename(output_path),
                    }
                # --- AUTO-RÉPARATION ---
                log.info("  [franime] Vérification de l'intégrité : %s", os.path.basename(filepath))
                
                # 1. Vérifier si c'est une vraie vidéo lisible
                probe_cmd = [ffprobe_exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filepath]
                probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
                
                is_valid = False
                try:
                    duration = float(probe_res.stdout.strip())
                    if duration > 10: # On considère que c'est valide si > 10s
                        is_valid = True
                except ValueError:
                    pass

                if not is_valid:
                    log.error(
                        "  [franime] Vidéo corrompue ou illisible (%s). Tentative de réparation...",
                        os.path.basename(filepath),
                    )
                    fixed_path = filepath.replace(".mp4", "_fixed.mp4")
                    # Tentative de remuxage pour réparer le container
                    fix_cmd = [ffmpeg_exe, "-y", "-i", filepath, "-c", "copy", "-map", "0:v", "-map", "0:a?", fixed_path]
                    fix_res = subprocess.run(fix_cmd, capture_output=True)
                    
                    if fix_res.returncode == 0 and os.path.exists(fixed_path) and os.path.getsize(fixed_path) > 1000000:
                        os.remove(filepath)
                        os.rename(fixed_path, filepath)
                        log.success('  [franime] ✓ Réparation réussie.')
                    else:
                        if os.path.exists(fixed_path): os.remove(fixed_path)
                        log.error('  [franime] ✗ Réparation échouée. Suppression pour retenter.')
                        os.remove(filepath)
                        raise Exception("Vidéo corrompue et non réparable.")

                return {
                    "success": True,
                    "filename": os.path.basename(filepath),
                    "filepath": filepath,
                    "folder": os.path.basename(output_path)
                }
        except Exception as e:
            log.error('  [franime] Erreur téléchargement/réparation : %s', e)
            if 'filepath' in locals() and filepath and os.path.exists(filepath):
                os.remove(filepath)
            raise e

    return {"success": False, "error": "Échec final"}


def _find_chrome() -> str | None:
    path = find_chrome_binary()
    return str(path) if path else None


def _chrome_user_data_dir() -> str | None:
    """Retourne le PROFIL UTILISATEUR RÉEL de Chrome (pour copier cookies), PAS le profil DowFlow."""
    import platform
    if platform.system() == "Windows":
        return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")
    if platform.system() == "Darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    return os.path.expanduser("~/.config/google-chrome")


def _resolve_ffmpeg_dir_proxy() -> str | None:
    return _resolve_ffmpeg_dir()
