import os
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory, url_for
from werkzeug.utils import secure_filename

from config import (
    DOWNLOAD_FOLDER,
    LOCAL_MEDIA_TTL_SECONDS,
    LOCAL_MEDIA_MAX_ENTRIES,
    ALLOWED_LOCAL_EXTENSIONS,
    PREVIEWABLE_EXTENSIONS,
    AUDIO_OUTPUT_FORMATS,
)
from utils import has_ffmpeg, is_http_url
from playlist import download_media, get_frontend_config
from logger import get_logger
from jobs import start_download_job, get_job, list_jobs
from database import download_list, download_count_by_status
from menu_config import export_menu, load_menu, rename_menu
from tunnels import TunnelError, slugify, start as start_tunnel, status as tunnel_status, stop as stop_tunnel
import desktop_build
import music

log = get_logger("app")

app = Flask(__name__)

DOWNLOAD_FOLDER.mkdir(exist_ok=True)

# Catégories du grand menu non encore implementees
COMING_SOON_CATEGORIES: dict[str, tuple[str, str]] = {
    "film": (
        "Film / Serie",
        "Telechargement de films et series (multi-saisons, pistes audio, sous-titres).",
    ),
    "anime": (
        "Anime / Dessin anime",
        "Animes, dessins animes et catalogues de diffusion en continu.",
    ),
    "image": (
        "Manhwa / Manga / BD / Image",
        "Chapitres, planches et images (manhwa, manga, bandes dessinees).",
    ),
}

LOCAL_MEDIA_REGISTRY: dict[str, tuple[Path, float]] = {}
# Verrou (reentrant) : le registre est partage entre les threads Flask.
_LOCAL_MEDIA_LOCK = threading.RLock()
DEBUG_MODE = os.environ.get("DOWFLOW_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
if DEBUG_MODE:
    log.info("Mode DOWFLOW_DEBUG actif (logs console DEBUG)")


def is_allowed_local_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in ALLOWED_LOCAL_EXTENSIONS


def cleanup_local_media_registry() -> None:
    # Appelable seul ou depuis register/serve (deja verrouilles) : RLock autorise la reentrance.
    with _LOCAL_MEDIA_LOCK:
        now = time.time()
        expired_tokens = [
            token
            for token, (_, created_at) in list(LOCAL_MEDIA_REGISTRY.items())
            if now - created_at > LOCAL_MEDIA_TTL_SECONDS
        ]
        for token in expired_tokens:
            LOCAL_MEDIA_REGISTRY.pop(token, None)

        if len(LOCAL_MEDIA_REGISTRY) <= LOCAL_MEDIA_MAX_ENTRIES:
            return

        tokens_by_age = sorted(
            LOCAL_MEDIA_REGISTRY.items(),
            key=lambda item: item[1][1],
        )
        overflow = len(LOCAL_MEDIA_REGISTRY) - LOCAL_MEDIA_MAX_ENTRIES
        for token, _ in tokens_by_age[:overflow]:
            LOCAL_MEDIA_REGISTRY.pop(token, None)


def register_local_media(source_path: Path) -> dict[str, str]:
    cleanup_local_media_registry()
    token = uuid.uuid4().hex
    with _LOCAL_MEDIA_LOCK:
        LOCAL_MEDIA_REGISTRY[token] = (source_path.resolve(), time.time())
    return {
        "filename": source_path.name,
        "filepath": str(source_path),
        "file_url": f"/media-local/{token}",
    }


@app.route("/")
def index():
    """Grand menu : choix du type de telechargement."""
    menu = load_menu()
    return render_template("menu.html", has_ffmpeg=has_ffmpeg(), menu=menu, export_scope="root", export_name=menu["name"])


@app.get("/api/menu")
def menu_config_api():
    return jsonify(load_menu())


@app.post("/api/menu/rename")
def rename_menu_api():
    payload = request.get_json(silent=True) or {}
    menu_id = str(payload.get("id", "")).strip()
    label = str(payload.get("label", "")).strip()
    if not menu_id or not label:
        return jsonify({"success": False, "error": "Un identifiant et un nom sont requis."}), 400
    try:
        return jsonify({"success": True, "menu": rename_menu(menu_id, label)})
    except KeyError:
        return jsonify({"success": False, "error": "Ce menu n'existe pas."}), 404


@app.get("/api/menu/export")
def export_menu_api():
    menu_id = request.args.get("id", "root").strip()
    requested_name = request.args.get("name", "").strip()
    try:
        payload = export_menu(menu_id)
    except KeyError:
        return jsonify({"success": False, "error": "Ce menu n'existe pas."}), 404
    if requested_name:
        payload["name"] = requested_name[:120]
    filename = secure_filename(payload["name"]) or "dowflow-menu"
    response = jsonify(payload)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}.json"'
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


PUBLISHER_PATHS = {
    "root": "/",
    "video": "/video",
    "music": "/music",
    "film": "/film",
    "anime": "/anime",
    "image": "/image",
}


@app.get("/api/publisher/link")
def publisher_link():
    scope = request.args.get("scope", "root").strip()
    name = request.args.get("name", "DowFlow").strip()[:120] or "DowFlow"
    provider = request.args.get("provider", "local").strip()
    if scope not in PUBLISHER_PATHS:
        return jsonify({"success": False, "error": "Cet espace n'existe pas."}), 404
    public_url = request.host_url.rstrip("/") + f"/{slugify(name)}"
    if scope != "root":
        public_url += PUBLISHER_PATHS[scope]
    commands = {
        "cloudflare": "cloudflared tunnel --url http://127.0.0.1:5001",
        "ngrok": "ngrok http 5001",
        "other": "Utilisez votre tunnel HTTP/HTTPS vers le port 5001.",
        "local": "Utilisez cette URL sur cet ordinateur ou son adresse IP locale.",
    }
    return render_template(
        "publisher_result.html",
        export_name=name,
        export_scope=scope,
        public_url=public_url,
        provider=provider,
        tunnel_command=commands.get(provider, commands["other"]),
        mode="link",
    )


@app.post("/api/publisher/tunnel/start")
def publisher_tunnel_start():
    payload = request.get_json(silent=True) or {}
    provider = str(payload.get("provider", "")).strip()
    scope = str(payload.get("scope", "root")).strip()
    name = str(payload.get("name", "DowFlow")).strip()
    if scope not in PUBLISHER_PATHS:
        return jsonify({"success": False, "error": "Cet espace n'existe pas."}), 404
    try:
        return jsonify({"success": True, **start_tunnel(provider, scope, name)})
    except TunnelError as error:
        return jsonify({"success": False, "error": str(error)}), 400


@app.get("/api/publisher/tunnel/status")
def publisher_tunnel_status():
    return jsonify(tunnel_status())


@app.post("/api/publisher/tunnel/stop")
def publisher_tunnel_stop():
    return jsonify({"success": True, **stop_tunnel()})


@app.post("/api/publisher/build/windows")
def publisher_windows_build():
    try:
        return jsonify({"success": True, **desktop_build.start()})
    except (OSError, RuntimeError) as error:
        return jsonify({"success": False, "error": str(error)}), 400


@app.get("/api/publisher/build/windows/status")
def publisher_windows_build_status():
    return jsonify(desktop_build.status())


@app.get("/api/publisher/build/windows/download/<path:filename>")
def publisher_windows_build_download(filename):
    safe_name = secure_filename(filename)
    build_file = (Path(__file__).resolve().parent / "dist" / safe_name).resolve()
    dist_folder = (Path(__file__).resolve().parent / "dist").resolve()
    if not safe_name or build_file.parent != dist_folder or not build_file.is_file():
        abort(404)
    return send_file(build_file, as_attachment=True, download_name=safe_name)


@app.get("/api/publisher/app")
def publisher_app():
    scope = request.args.get("scope", "root").strip()
    name = request.args.get("name", "DowFlow").strip()[:120] or "DowFlow"
    if scope not in PUBLISHER_PATHS:
        return jsonify({"success": False, "error": "Cet espace n'existe pas."}), 404
    return render_template(
        "publisher_result.html",
        export_name=name,
        export_scope=scope,
        public_url=request.host_url.rstrip("/") + f"/{slugify(name)}{PUBLISHER_PATHS[scope] if scope != 'root' else ''}",
        provider="PWA",
        tunnel_command="Installez l’espace depuis le menu du navigateur : Installer l’application.",
        mode="app",
        manifest_url=url_for("publisher_manifest", scope=scope, name=name),
    )


@app.get("/api/publisher/manifest")
@app.get("/manifest.webmanifest")
def publisher_manifest():
    scope = request.args.get("scope", "root").strip()
    name = request.args.get("name", "DowFlow").strip()[:120] or "DowFlow"
    if scope not in PUBLISHER_PATHS:
        return jsonify({"success": False, "error": "Cet espace n'existe pas."}), 404
    manifest = {
        "name": name,
        "short_name": name[:24],
        "start_url": f"/{slugify(name)}{PUBLISHER_PATHS[scope] if scope != 'root' else ''}",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0a0a0a",
        "theme_color": "#c96034",
        "icons": [{"src": "/static/favicon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }
    response = jsonify(manifest)
    response.headers["Content-Type"] = "application/manifest+json; charset=utf-8"
    return response


@app.get("/service-worker.js")
def service_worker():
    return send_from_directory("static", "service-worker.js", mimetype="application/javascript")


@app.get("/<export_slug>")
def named_root_page(export_slug):
    if export_slug in {"api", "static", "service-worker.js", "manifest.webmanifest"}:
        abort(404)
    menu = load_menu()
    return render_template("menu.html", has_ffmpeg=has_ffmpeg(), menu=menu, export_scope="root", export_name=menu["name"])


@app.get("/<export_slug>/<scope>")
def named_scope_page(export_slug, scope):
    if export_slug in {"api", "static"} or scope not in PUBLISHER_PATHS or scope == "root":
        abort(404)
    if scope == "video":
        return video_page()
    if scope == "music":
        return music_page()
    label, description = COMING_SOON_CATEGORIES.get(scope, (scope.title(), ""))
    return render_template(
        "coming_soon.html", category=scope, label=label, description=description,
        has_ffmpeg=has_ffmpeg(), export_scope=scope, export_name=label,
    )


@app.route("/video")
def video_page():
    """Telechargeur video / audio generique (template historique)."""
    return render_template("dowload.html", has_ffmpeg=has_ffmpeg(), export_scope="video", export_name="DowFlow Vidéo")


@app.route("/music")
def music_page():
    """Espace musique : recherche, populaires, telechargement, bibliotheque."""
    return render_template("music.html", has_ffmpeg=has_ffmpeg(), export_scope="music", export_name="DowFlow Musique")


@app.route("/film")
@app.route("/anime")
@app.route("/image")
def coming_soon_page():
    """Categories du menu pas encore disponibles."""
    category = request.path.strip("/")
    label, description = COMING_SOON_CATEGORIES.get(
        category, ("Bientot disponible", "Cette categorie arrive prochainement.")
    )
    return render_template(
        "coming_soon.html",
        category=category,
        label=label,
        description=description,
        has_ffmpeg=has_ffmpeg(),
        export_scope=category,
        export_name=label,
    )


# ─────────────────────────────  API Musique  ─────────────────────────────────

@app.route("/api/music/search", methods=["POST"])
def music_search():
    data = request.get_json(silent=True) or {}
    query = str(data.get("query") or "").strip()
    if not query:
        return jsonify({"success": False, "error": "Requete vide."}), 400
    try:
        limit = int(data.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 50))
    tracks = music.search_music(query, limit)
    return jsonify({"success": True, "query": query, "tracks": tracks})


@app.route("/api/music/popular")
def music_popular():
    try:
        limit = int(request.args.get("limit") or 24)
    except (TypeError, ValueError):
        limit = 24
    limit = max(1, min(limit, 50))
    return jsonify({"success": True, "tracks": music.popular_music(limit)})


@app.route("/api/music/download", methods=["POST"])
def music_download():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "Aucun morceau selectionne."}), 400

    audio_format = str(data.get("audio_format") or "mp3")
    if audio_format not in AUDIO_OUTPUT_FORMATS:
        audio_format = "mp3"

    job_id = start_download_job(
        music.download_music_track,
        url,
        output_path=str(DOWNLOAD_FOLDER),
        audio_format=audio_format,
        with_cover=bool(data.get("with_cover", True)),
        title=data.get("title"),
        artist=data.get("artist"),
    )
    return jsonify({"success": True, "async": True, "job_id": job_id})


@app.route("/api/music/library")
def music_library():
    try:
        limit = int(request.args.get("limit") or 200)
    except (TypeError, ValueError):
        limit = 200
    return jsonify({"success": True, "items": music.list_library(max(1, min(limit, 500)))})


@app.route("/api/music/delete", methods=["POST"])
def music_delete():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename")
    if not filename:
        return jsonify({"success": False, "error": "Fichier non specifie."}), 400
    
    success = music.delete_track(filename)
    if success:
        return jsonify({"success": True, "message": "Morceau supprime."})
    return jsonify({"success": False, "error": "Impossible de supprimer le morceau."}), 500


@app.route("/check-ffmpeg")
def check_ffmpeg():
    return jsonify({"has_ffmpeg": has_ffmpeg()})


@app.route("/history")
def history():
    """Historique SQLite des telechargements (+ stats)."""
    try:
        limit = request.args.get("limit", default=50, type=int)
        limit = max(1, min(limit or 50, 200))
        status = (request.args.get("status") or "").strip() or None
        rows = download_list(limit=limit, status=status)
        return jsonify(
            {
                "success": True,
                "counts": download_count_by_status(),
                "items": rows,
            }
        )
    except Exception as e:
        log.error("[history] %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/jobs", methods=["GET"])
def jobs_list():
    return jsonify({"success": True, "jobs": list_jobs(limit=50)})


@app.route("/jobs/<job_id>", methods=["GET"])
def jobs_status(job_id: str):
    job = get_job(job_id)
    if job is None:
        return jsonify({"success": False, "error": "Job introuvable."}), 404
    return jsonify({"success": True, "job": job})


@app.route("/download-async", methods=["POST"])
def download_async():
    """Lance un telechargement en arriere-plan ; retourne un job_id."""
    try:
        data = request.get_json() or {}
        source_value = (data.get("url") or "").strip().replace("\n", "").replace("\r", "")
        if not source_value:
            return jsonify({"success": False, "error": "URL manquante."}), 400

        # Chemins locaux : reponse immediate (pas de job)
        local_path = Path(source_value).expanduser()
        if not is_http_url(source_value) and is_allowed_local_file(local_path):
            registered = register_local_media(local_path)
            return jsonify(
                {
                    "success": True,
                    "async": False,
                    "filename": registered["filename"],
                    "file_url": registered["file_url"],
                    "source_type": "local",
                    "source_label": str(local_path),
                    "previewable": local_path.suffix.lower() in PREVIEWABLE_EXTENSIONS,
                    "message": "Fichier local pret sans copie.",
                }
            )

        if not is_http_url(source_value):
            return jsonify({"success": False, "error": "URL invalide ou chemin local introuvable."}), 400

        download_type = data.get("type", "video")
        quality = data.get("quality", "best")
        video_format = data.get("video_format", "mp4")
        audio_format = data.get("audio_format", "mp3")
        language = data.get("language") or None
        if isinstance(language, str):
            language = language.strip() or None
        write_subtitles = bool(data.get("write_subtitles", False))
        adaptive_quality = bool(data.get("adaptive_quality", True))

        job_id = start_download_job(
            download_media,
            source_value,
            output_path=str(DOWNLOAD_FOLDER),
            download_type=download_type,
            quality=quality,
            video_format=video_format,
            audio_format=audio_format,
            language=language,
            write_subtitles=write_subtitles,
            adaptive_quality=adaptive_quality,
        )
        log.info("[download-async] job=%s url=%s", job_id, source_value[:120])
        return jsonify({"success": True, "async": True, "job_id": job_id})
    except Exception as e:
        log.error("[download-async] %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/config")
def frontend_config():
    """Retourne la liste statique des formats, qualités et langues au frontend."""
    payload = get_frontend_config()
    payload["has_ffmpeg"] = has_ffmpeg()
    return jsonify(payload)


@app.route("/media/<path:filename>")
def serve_media(filename: str):
    return send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=False)


@app.route("/media-local/<token>")
def serve_local_media(token: str):
    cleanup_local_media_registry()
    with _LOCAL_MEDIA_LOCK:
        entry = LOCAL_MEDIA_REGISTRY.get(token)
        if entry is None:
            abort(404)
        source_path, _ = entry
        if not source_path.is_file():
            LOCAL_MEDIA_REGISTRY.pop(token, None)
            abort(404)
        LOCAL_MEDIA_REGISTRY[token] = (source_path, time.time())
    return send_file(source_path, as_attachment=False)


@app.route("/open-folder")
def open_folder():
    try:
        os.startfile(str(DOWNLOAD_FOLDER))  # type: ignore[attr-defined]
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/upload-local", methods=["POST"])
def upload_local():
    try:
        file = request.files.get("file")
        if file is None or not file.filename:
            return jsonify({"success": False, "error": "Aucun fichier local recu."}), 400

        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_LOCAL_EXTENSIONS:
            return jsonify({"success": False, "error": "Format de fichier non supporte."}), 400

        safe_name = secure_filename(file.filename) or f"local-{uuid.uuid4().hex}{suffix}"
        stored_name = f"{uuid.uuid4().hex[:8]}-{safe_name}"
        destination = DOWNLOAD_FOLDER / stored_name
        file.save(destination)

        return jsonify(
            {
                "success": True,
                "filename": stored_name,
                "file_url": f"/media/{stored_name}",
                "source_type": "local",
                "source_label": file.filename,
                "message": "Fichier local charge avec succes.",
                "previewable": Path(stored_name).suffix.lower() in PREVIEWABLE_EXTENSIONS,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": f"Upload local impossible: {e}"}), 500


@app.route("/url-options", methods=["POST"])
def url_options():
    """Retourne le profil d'options pour la premiere URL (multi-sites)."""
    try:
        data = request.get_json() or {}
        url = (data.get("url") or "").strip()
        log.info("[url-options] url=%s", url or "(vide)")
        if not url:
            return jsonify({"success": False, "error": "URL manquante."}), 400
        from url_expanders import options_payload
        payload = options_payload(url)
        log.info(
            "[url-options] site=%s available=%s options=%d",
            payload.get("site_id"),
            payload.get("available"),
            len(payload.get("options") or []),
        )
        return jsonify(payload)
    except Exception as e:
        log.error("[url-options] ERREUR: %s", e, exc_info=True)
        return jsonify({"success": False, "error": f"Erreur système: {e}"}), 500


@app.route("/expand-url", methods=["POST"])
def expand_url_route():
    """Expand une URL source en liste d'URLs selon les options du site."""
    try:
        data = request.get_json() or {}
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"success": False, "error": "URL manquante."}), 400
        options = data.get("options") or {}
        # Compat ancien client
        if "current_only" in data and "scope" not in options:
            options["scope"] = "season" if data.get("current_only") else "all"
            options["current_only"] = bool(data.get("current_only"))

        log.info("[expand-url] url=%s options=%s", url, options)
        from url_expanders import expand_url
        result = expand_url(url, options)
        status = 200 if result.success else 400
        log.info(
            "[expand-url] success=%s site=%s count=%s error=%s",
            result.success,
            result.site_id,
            result.count or len(result.urls),
            result.error,
        )
        return jsonify(result.to_dict()), status
    except Exception as e:
        log.error("[expand-url] ERREUR: %s", e, exc_info=True)
        return jsonify({"success": False, "error": f"Erreur système: {e}"}), 500


@app.route("/fetch-episodes", methods=["POST"])
def fetch_episodes():
    """Alias historique → /expand-url (Franime)."""
    try:
        data = request.get_json() or {}
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"success": False, "error": "URL manquante."}), 400
        options = {
            "scope": "season" if data.get("current_only") else "all",
            "current_only": bool(data.get("current_only")),
        }
        log.info("[fetch-episodes] url=%s options=%s", url, options)
        from url_expanders import expand_url
        result = expand_url(url, options)
        status = 200 if result.success else 400
        log.info(
            "[fetch-episodes] success=%s count=%s error=%s",
            result.success,
            result.count or len(result.urls),
            result.error,
        )
        return jsonify(result.to_dict()), status
    except Exception as e:
        log.error("[fetch-episodes] ERREUR: %s", e, exc_info=True)
        return jsonify({"success": False, "error": f"Erreur système: {e}"}), 500


@app.route("/download", methods=["POST"])
def download():
    try:
        data = request.get_json() or {}
        # Nettoyage agressif de l'URL pour supprimer les retours a la ligne accidentels
        source_value = data.get("url", "").strip().replace("\n", "").replace("\r", "")
        download_type = data.get("type", "video")
        quality = data.get("quality", "best")
        video_format = data.get("video_format", "mp4")
        audio_format = data.get("audio_format", "mp3")
        language = data.get("language") or None
        if isinstance(language, str):
            language = language.strip() or None
        write_subtitles = bool(data.get("write_subtitles", False))
        adaptive_quality = bool(data.get("adaptive_quality", True))
        log.info(
            "[download] type=%s quality=%s vfmt=%s afmt=%s lang=%s adaptive=%s url=%s",
            download_type, quality, video_format, audio_format,
            language or "(auto)", adaptive_quality, source_value[:150] if source_value else "(vide)",
        )

        if not source_value:
            return jsonify({"success": False, "error": "Merci de renseigner une URL ou un chemin local."}), 400

        local_path = Path(source_value).expanduser()
        if not is_http_url(source_value) and is_allowed_local_file(local_path):
            registered = register_local_media(local_path)
            return jsonify(
                {
                    "success": True,
                    "message": "Fichier local pret sans copie.",
                    "filename": registered["filename"],
                    "file_url": registered["file_url"],
                    "source_type": "local",
                    "source_label": str(local_path),
                    "previewable": local_path.suffix.lower() in PREVIEWABLE_EXTENSIONS,
                }
            )

        if not is_http_url(source_value):
            return jsonify({"success": False, "error": "URL invalide ou chemin local introuvable."}), 400

        result = download_media(
            source_value,
            output_path=str(DOWNLOAD_FOLDER),
            download_type=download_type,
            quality=quality,
            video_format=video_format,
            audio_format=audio_format,
            language=language,
            write_subtitles=write_subtitles,
            adaptive_quality=adaptive_quality,
        )

        filename = result.get("filename")
        relative_path = result.get("relative_path") or filename
        
        if result.get("skipped"):
            reason = result.get("reason")
            if reason == "already_exists":
                reason_msg = "Déjà téléchargé."
            elif reason == "page_blocked":
                reason_msg = "Page bloquée par le serveur distant."
            else:
                reason_msg = "Introuvable ou indisponible."
            return jsonify({
                "success": True,
                "skipped": True,
                "reason": reason,
                "message": f"Episode sauté : {reason_msg}",
                "filename": filename,
                "source_label": source_value
            })

        if not filename:
            return jsonify({"success": False, "error": result.get("error", "Fichier telecharge mais introuvable.")}), 500

        from urllib.parse import urlparse

        domain = urlparse(source_value).netloc.replace("www.", "") or "remote"
        previewable = Path(filename).suffix.lower() in PREVIEWABLE_EXTENSIONS
        payload = {
            "success": True,
            "message": f"Telecharge depuis {domain} : {filename}",
            "filename": filename,
            "file_url": f"/media/{relative_path}",
            "source_type": domain,
            "source_label": source_value,
            "previewable": previewable,
            "storage_folder": result.get("folder"),
        }

        if download_type == "audio":
            payload["message"] = f"Audio telecharge : {filename}"
        elif not previewable:
            payload["message"] = f"Telecharge : {filename} (apercu non supporte par le navigateur)"

        return jsonify(payload)

    except Exception as e:
        error_msg = str(e).strip() or type(e).__name__
        log.error("[download] Échec : %s", error_msg, exc_info=True)
        if "ffmpeg" in error_msg.lower() or "ffprobe" in error_msg.lower():
            return jsonify(
                {
                    "success": False,
                    "error": "FFmpeg est requis mais n'est pas installe. Installe FFmpeg pour continuer.",
                    "is_ffmpeg_error": True,
                }
            ), 500
        return jsonify({"success": False, "error": f"Echec du telechargement: {error_msg}"}), 500


if __name__ == "__main__":
    from logger import banner
    banner(log, "DowFlow — Multi-Site Media Downloader")

    # Analyse rapide du réseau au lancement
    try:
        from playlist import _measure_network_speed_mbps, detect_connection_tier
        log.info("Analyse du réseau au démarrage...")
        speed = _measure_network_speed_mbps()
        if speed > 0:
            log.success("Débit détecté : %.2f Mbps", speed)
        else:
            log.warning("Débit non détecté (vérifiez votre connexion).")
        # Palier de connexions (8/16/32/64/128) selon la puissance du PC et le débit.
        detect_connection_tier(speed)
    except Exception:
        pass

    log.success("Serveur démarré sur http://127.0.0.1:5001")
    if DEBUG_MODE:
        log.info("DEBUG: historique=/history  jobs=/jobs  async=/download-async")
    log.info("Ctrl+C pour arrêter")
    app.run(
        debug=DEBUG_MODE,
        host=os.environ.get("DOWFLOW_HOST", "127.0.0.1"),
        port=int(os.environ.get("DOWFLOW_PORT", "5001")),
        use_reloader=False,
        threaded=True,
    )
