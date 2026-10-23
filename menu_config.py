import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any


MENU_CONFIG_PATH = Path(__file__).resolve().parent / "dowflow_menu.json"

DEFAULT_MENU: dict[str, Any] = {
    "name": "DowFlow",
    "description": "Téléchargement vidéo et audio",
    "items": [
        {"id": "video", "label": "Vidéo", "description": "Télécharge une vidéo ou un audio depuis une URL.", "href": "/video", "available": True},
        {"id": "music", "label": "Musique", "description": "Recherche, téléchargement et bibliothèque musicale.", "href": "/music", "available": True},
        {"id": "film", "label": "Film / Série", "description": "Films et séries avec audio et sous-titres.", "href": "/film", "available": False},
        {"id": "anime", "label": "Anime / Dessin animé", "description": "Animes et dessins animés en VF ou VOSTFR.", "href": "/anime", "available": False},
        {"id": "image", "label": "Manhwa / Manga / BD / Image", "description": "Chapitres, planches et images.", "href": "/image", "available": False},
    ],
}


def _clean_text(value: Any, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:120] or fallback


def _normalize_config(raw: Any) -> dict[str, Any]:
    config = deepcopy(DEFAULT_MENU)
    if not isinstance(raw, dict):
        return config
    config["name"] = _clean_text(raw.get("name"), config["name"])
    config["description"] = _clean_text(raw.get("description"), config["description"])
    if not isinstance(raw.get("items"), list):
        return config

    known_items = {item["id"]: item for item in config["items"]}
    normalized_items = []
    for raw_item in raw["items"]:
        if not isinstance(raw_item, dict) or raw_item.get("id") not in known_items:
            continue
        item = deepcopy(known_items[raw_item["id"]])
        item["label"] = _clean_text(raw_item.get("label"), item["label"])
        item["description"] = _clean_text(raw_item.get("description"), item["description"])
        normalized_items.append(item)
    if normalized_items:
        config["items"] = normalized_items
    return config


def load_menu() -> dict[str, Any]:
    try:
        raw = json.loads(MENU_CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return deepcopy(DEFAULT_MENU)
    return _normalize_config(raw)


def save_menu(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_config(config)
    MENU_CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


def rename_menu(menu_id: str, label: str) -> dict[str, Any]:
    config = load_menu()
    if menu_id == "root":
        config["name"] = _clean_text(label, config["name"])
    else:
        item = next((item for item in config["items"] if item["id"] == menu_id), None)
        if item is None:
            raise KeyError(menu_id)
        item["label"] = _clean_text(label, item["label"])
    return save_menu(config)


