"""Expansion d'URLs Franime → liste d'épisodes."""
from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import requests

from .base import ExpandResult, OptionDef, SiteProfile
from config import LANGUAGES, normalize_language

FRANIME_HOSTS = {"franime.fr"}
CATALOG_URL = "https://api.franime.fr/api/animes/"
_CATALOG_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_CATALOG_TTL_SECONDS = 600


def supports(url: str, host: str) -> bool:
    return host in FRANIME_HOSTS or "franime.fr" in host


def _normalize_lang(value: str | None) -> str:
    lang = (value or "vf").strip().lower()
    if lang in {"vostfr", "vost", "sub", "subs"}:
        return "vo"
    if lang in {"vf", "vo"}:
        return lang
    return lang


def _display_lang(lang: str) -> str:
    if lang == "vo":
        return "VOSTFR"
    if lang in LANGUAGES:
        return LANGUAGES[lang]["label"]
    return lang.upper()


def profile_for(url: str, host: str) -> SiteProfile | None:
    if not supports(url, host):
        return None

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    try:
        season = max(1, int(query.get("s", ["1"])[0] or "1"))
    except ValueError:
        season = 1
    raw_lang = (query.get("lang", ["vf"])[0] or "vf").lower()
    lang = normalize_language(raw_lang) or _normalize_lang(raw_lang)
    extra_keys = sorted(
        key
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"s", "ep", "lang", "anime_id"}
    )
    extras = f" Paramètres conservés : {', '.join(extra_keys)}." if extra_keys else ""

    # ── Langues : VF + VO en premier, puis toutes les ISO triées par label ──
    top_langs = [("vf", "VF"), ("vo", "VO / VOSTFR")]
    all_langs: list[Any] = [*top_langs]
    seen = {"vf", "vo"}
    for code, meta in LANGUAGES.items():
        if code in seen:
            continue
        all_langs.append((code, meta["label"]))
        seen.add(code)
    lang_choices = [{"value": v, "label": l} for v, l in all_langs]

    return SiteProfile(
        site_id="franime",
        label="Franime",
        available=True,
        help_text=(
            f"Détecté : saison {season}, langue {_display_lang(lang)}. "
            "Trouver / Rechercher génère la liste d'épisodes à partir de la première URL."
            f"{extras}"
        ),
        options=[
            OptionDef(
                key="scope",
                label="Portée",
                kind="select",
                default="season",
                help_text="Saison de l'URL, ou toutes les saisons connues.",
                choices=[
                    {"value": "season", "label": "Saison de l'URL seulement"},
                    {"value": "all", "label": "Toutes les saisons"},
                ],
            ),
            OptionDef(
                key="lang",
                label="Langue",
                kind="select",
                default=lang if lang in {"vf", "vo"} or lang in LANGUAGES else "vf",
                help_text="VF, VOSTFR, ou code ISO de n'importe quelle langue. Pris depuis l'URL si présent.",
                choices=lang_choices,
            ),
            OptionDef(
                key="only_available",
                label="Uniquement épisodes dispo",
                kind="toggle",
                default=True,
                help_text="Ignore les épisodes sans lecteur pour la langue choisie.",
            ),
            OptionDef(
                key="max_episodes",
                label="Limite d'épisodes",
                kind="number",
                default=0,
                min_value=0,
                max_value=500,
                help_text="0 = pas de limite. Utile pour tester sans générer 100+ URLs.",
            ),
        ],
    )


def expand(url: str, options: dict[str, Any]) -> ExpandResult:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    anime_id = query.get("anime_id", [None])[0]
    try:
        requested_season = max(1, int(query.get("s", ["1"])[0] or "1"))
    except ValueError:
        requested_season = 1

    scope = str(options.get("scope") or "season").lower()
    if options.get("current_only"):
        scope = "season"

    # Priorité : option UI > paramètre URL > vf
    raw_lang = options.get("lang")
    if raw_lang in (None, ""):
        raw_lang = query.get("lang", ["vf"])[0]
    lang = _normalize_lang(str(raw_lang or "vf"))

    only_available = bool(options.get("only_available", True))
    try:
        max_episodes = int(options.get("max_episodes") or 0)
    except (TypeError, ValueError):
        max_episodes = 0

    path_parts = [p for p in parsed.path.split("/") if p]
    anime_slug = path_parts[-1] if path_parts else "anime"
    base_params = dict(parse_qsl(parsed.query, keep_blank_values=True))

    # 1) Catalogue officiel api/animes/
    api_result = _from_catalog(
        anime_id=anime_id,
        anime_slug=anime_slug,
        source_url=url,
        base_params=base_params,
        lang=lang,
        requested_season=requested_season,
        scope=scope,
        only_available=only_available,
        max_episodes=max_episodes,
    )
    if api_result.success:
        return api_result

    # 2) Jikan / MAL (approximation du nombre d'épisodes)
    mal_result = _from_jikan(
        anime_slug, anime_id, url, base_params, lang, requested_season, scope, max_episodes
    )
    if mal_result.success:
        return mal_result

    # 3) HTML __NEXT_DATA__
    html_result = _from_next_data(
        url, anime_id, base_params, lang, requested_season, scope, only_available, max_episodes
    )
    if html_result.success:
        return html_result

    # 4) Playwright
    pw_result = _from_playwright(
        url, anime_id, base_params, lang, requested_season, scope, only_available, max_episodes
    )
    if pw_result.success:
        return pw_result

    detail = " / ".join(
        part
        for part in [
            api_result.error,
            mal_result.error,
            html_result.error,
            pw_result.error,
        ]
        if part
    )
    return ExpandResult(
        success=False,
        site_id="franime",
        error=f"Impossible de trouver les épisodes Franime. {detail}".strip(),
    )


def _limit(urls: list[str], max_episodes: int) -> list[str]:
    if max_episodes and max_episodes > 0:
        return urls[:max_episodes]
    return urls


def _build_episode_url(source_url: str, base_params: dict[str, str], season: int, ep: int, lang: str) -> str:
    """Conserve tous les paramètres d'origine (anime_id, etc.) et met à jour s/ep/lang."""
    parsed = urlparse(source_url)
    params = dict(base_params)
    params["s"] = str(season)
    params["ep"] = str(ep)
    params["lang"] = lang
    return urlunparse(parsed._replace(query=urlencode(params)))


def _episode_has_lang(episode: Any, lang: str) -> bool:
    if not isinstance(episode, dict):
        return True
    lang_block = episode.get("lang") or episode.get("languages") or {}
    if not isinstance(lang_block, dict):
        return True
    # Franime : vo = VOSTFR ; parfois clés vf/vo seulement
    candidates = [lang]
    if lang == "vo":
        candidates.extend(["vostfr", "vost"])
    for key in candidates:
        entry = lang_block.get(key)
        if entry is None:
            continue
        if isinstance(entry, dict):
            lecteurs = entry.get("lecteurs") or entry.get("players") or []
            return bool(lecteurs)
        return bool(entry)
    # Si la langue demandée est absente mais d'autres existent → indispo
    if lang_block:
        return False
    return True


def _load_catalog() -> list[dict[str, Any]]:
    global _CATALOG_CACHE
    now = time.time()
    if _CATALOG_CACHE and now - _CATALOG_CACHE[0] < _CATALOG_TTL_SECONDS:
        return _CATALOG_CACHE[1]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
        "Referer": "https://franime.fr/",
        "Accept": "application/json",
    }
    res = requests.get(CATALOG_URL, headers=headers, timeout=45)
    if not res.ok:
        raise RuntimeError(f"API HTTP {res.status_code}")
    payload = res.json()
    if not isinstance(payload, list):
        raise RuntimeError("API: catalogue invalide.")
    _CATALOG_CACHE = (now, payload)
    return payload


def _find_anime(catalog: list[dict[str, Any]], anime_id: str | None, anime_slug: str) -> dict[str, Any] | None:
    if anime_id:
        for item in catalog:
            if str(item.get("id")) == str(anime_id):
                return item

    slug = (anime_slug or "").lower().replace("_", "-")
    tokens = [t for t in re.split(r"[-_\s]+", slug) if len(t) > 2]
    best = None
    best_score = 0
    for item in catalog:
        titles = [
            str(item.get("title") or ""),
            str(item.get("titleO") or ""),
            *(str(t) for t in (item.get("titles") or []) if t),
        ]
        blob = " ".join(titles).lower()
        score = sum(1 for t in tokens if t in blob)
        if score > best_score:
            best_score = score
            best = item
    if best_score >= max(1, len(tokens) // 2):
        return best
    return None


def _urls_from_seasons(
    seasons: list[Any],
    source_url: str,
    base_params: dict[str, str],
    lang: str,
    requested_season: int,
    scope: str,
    only_available: bool,
) -> tuple[list[str], str]:
    all_urls: list[str] = []
    summary: list[str] = []
    for s_idx, season in enumerate(seasons):
        s_num = s_idx + 1
        if scope == "season" and s_num != requested_season:
            continue
        if not isinstance(season, dict):
            continue
        episodes = season.get("episodes", [])
        if isinstance(episodes, int):
            ep_count = episodes
            for ep_num in range(1, ep_count + 1):
                all_urls.append(_build_episode_url(source_url, base_params, s_num, ep_num, lang))
            summary.append(f"S{s_num} ({ep_count} eps)")
            continue

        kept = 0
        for ep_idx, episode in enumerate(episodes or []):
            ep_num = ep_idx + 1
            if only_available and not _episode_has_lang(episode, lang):
                continue
            all_urls.append(_build_episode_url(source_url, base_params, s_num, ep_num, lang))
            kept += 1
        summary.append(f"S{s_num} ({kept} eps {_display_lang(lang)})")
    return all_urls, " + ".join(summary) if summary else "aucun"


def _from_catalog(
    anime_id: str | None,
    anime_slug: str,
    source_url: str,
    base_params: dict[str, str],
    lang: str,
    requested_season: int,
    scope: str,
    only_available: bool,
    max_episodes: int,
) -> ExpandResult:
    try:
        catalog = _load_catalog()
    except Exception as exc:
        return ExpandResult(success=False, error=f"API catalogue: {exc}")

    anime = _find_anime(catalog, anime_id, anime_slug)
    if anime is None:
        return ExpandResult(success=False, error="API catalogue: anime introuvable.")

    seasons = anime.get("saisons") or anime.get("seasons") or []
    if not seasons:
        return ExpandResult(success=False, error="API catalogue: pas de saisons.")

    # Assure anime_id dans les params générés
    if anime.get("id") is not None:
        base_params = dict(base_params)
        base_params["anime_id"] = str(anime["id"])

    urls, summary = _urls_from_seasons(
        seasons, source_url, base_params, lang, requested_season, scope, only_available
    )
    urls = _limit(urls, max_episodes)
    if not urls:
        return ExpandResult(
            success=False,
            error=f"API catalogue: aucun épisode pour {_display_lang(lang)}.",
        )

    title = anime.get("title") or anime.get("titleO") or anime_slug
    return ExpandResult(
        success=True,
        urls=urls,
        count=len(urls),
        title=f"{title} — {summary}",
        site_id="franime",
    )


def _from_jikan(
    anime_slug: str,
    anime_id: str | None,
    source_url: str,
    base_params: dict[str, str],
    lang: str,
    requested_season: int,
    scope: str,
    max_episodes: int,
) -> ExpandResult:
    try:
        search_name = anime_slug.replace("-", " ")
        search_res = requests.get(
            "https://api.jikan.moe/v4/anime",
            params={"q": search_name, "type": "tv", "order_by": "start_date", "sort": "asc", "limit": 10},
            timeout=8,
        )
        if not search_res.ok:
            return ExpandResult(success=False, error=f"MAL HTTP {search_res.status_code}")

        results = search_res.json().get("data", []) or []
        keywords = [k for k in anime_slug.split("-") if len(k) > 2]
        main_results = [
            r for r in results if any(k in (r.get("title") or "").lower() for k in keywords)
        ] or results[:3]

        if not main_results:
            return ExpandResult(success=False, error="MAL: aucun résultat.")

        seasons_to_generate = list(enumerate(main_results, start=1))
        if scope == "season":
            seasons_to_generate = [
                item for item in seasons_to_generate if item[0] == requested_season
            ] or [
                (
                    requested_season,
                    main_results[min(requested_season - 1, len(main_results) - 1)],
                )
            ]

        all_urls: list[str] = []
        summary_info: list[str] = []
        params = dict(base_params)
        if anime_id:
            params["anime_id"] = str(anime_id)
        for season_num, anime_entry in seasons_to_generate:
            ep_count = anime_entry.get("episodes") or 0
            if not ep_count:
                ep_count = 25
            for ep_num in range(1, ep_count + 1):
                all_urls.append(_build_episode_url(source_url, params, season_num, ep_num, lang))
            summary_info.append(f"S{season_num} ({ep_count} eps)")

        all_urls = _limit(all_urls, max_episodes)
        title = f"{main_results[0].get('title', anime_slug)} — {' + '.join(summary_info)}"
        return ExpandResult(
            success=True,
            urls=all_urls,
            count=len(all_urls),
            title=title,
            site_id="franime",
        )
    except Exception as exc:
        return ExpandResult(success=False, error=f"MAL: {exc}")


def _from_next_data(
    url: str,
    anime_id: str | None,
    base_params: dict[str, str],
    lang: str,
    requested_season: int,
    scope: str,
    only_available: bool,
    max_episodes: int,
) -> ExpandResult:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
        "Referer": "https://franime.fr/",
        "Accept-Language": "fr-FR,fr;q=0.9",
    }
    try:
        response = requests.get(url, headers=headers, timeout=12)
        if not response.ok:
            return ExpandResult(success=False, error=f"HTML HTTP {response.status_code}")
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            response.text,
            re.DOTALL,
        )
        if not match:
            return ExpandResult(success=False, error="HTML: pas de __NEXT_DATA__ (Cloudflare ?)")

        next_data = json.loads(match.group(1))
        props = next_data.get("props", {}).get("pageProps", {})
        anime_data = props.get("anime") or props.get("data") or {}
        seasons = anime_data.get("saisons") or []
        if not seasons:
            return ExpandResult(success=False, error="HTML: pas de saisons.")

        params = dict(base_params)
        if anime_id:
            params["anime_id"] = str(anime_id)
        title = anime_data.get("title") or "Franime"
        urls, summary = _urls_from_seasons(
            seasons, url, params, lang, requested_season, scope, only_available
        )
        urls = _limit(urls, max_episodes)
        return ExpandResult(
            success=True,
            urls=urls,
            count=len(urls),
            title=f"{title} — {summary}",
            site_id="franime",
        )
    except Exception as exc:
        return ExpandResult(success=False, error=f"HTML: {exc}")


def _from_playwright(
    url: str,
    anime_id: str | None,
    base_params: dict[str, str],
    lang: str,
    requested_season: int,
    scope: str,
    only_available: bool,
    max_episodes: int,
) -> ExpandResult:
    try:
        from playwright.sync_api import sync_playwright
        from config import BROWSER_HEADLESS, BROWSER_PROFILE_DIR
    except Exception as exc:
        return ExpandResult(success=False, error=f"Playwright indisponible: {exc}")

    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                str(BROWSER_PROFILE_DIR),
                headless=BROWSER_HEADLESS,
                locale="fr-FR",
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            for _ in range(20):
                content = page.content()
                if "__NEXT_DATA__" in content and "just a moment" not in page.title().lower():
                    break
                page.wait_for_timeout(1500)

            match = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                page.content(),
                re.DOTALL,
            )
            ctx.close()
            if not match:
                return ExpandResult(success=False, error="Playwright: __NEXT_DATA__ introuvable.")

            next_data = json.loads(match.group(1))
            props = next_data.get("props", {}).get("pageProps", {})
            anime_data = props.get("anime") or props.get("data") or {}
            seasons = anime_data.get("saisons") or []
            if not seasons:
                return ExpandResult(success=False, error="Playwright: pas de saisons dans la page.")

            params = dict(base_params)
            if anime_id:
                params["anime_id"] = str(anime_id)
            title = anime_data.get("title") or "Franime"
            urls, summary = _urls_from_seasons(
                seasons, url, params, lang, requested_season, scope, only_available
            )
            urls = _limit(urls, max_episodes)
            return ExpandResult(
                success=True,
                urls=urls,
                count=len(urls),
                title=f"{title} — {summary}",
                site_id="franime",
            )
    except Exception as exc:
        return ExpandResult(success=False, error=f"Playwright: {exc}")
