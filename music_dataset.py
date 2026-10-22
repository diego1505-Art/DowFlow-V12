"""
DowFlow — Point d'intégration d'un dataset musical local (prévu, non requis).

Un dataset local (par ex. les archives MusicBrainz / Tidal / Spotify / Deezer
issues du torrent « MusicBrainz Tidal Spotify Deezer Dataset ») pourra être
importé ici sous une forme interrogeable (ex. SQLite) pour alimenter la
recherche et les pochettes même hors-ligne.

Tant qu'aucun dataset n'est présent, `get_dataset()` renvoie `None` et le
service musique (`music.py`) fonctionne uniquement via YouTube + l'API Deezer.
Aucune dépendance au torrent n'est nécessaire pour utiliser la partie musique.

Pour brancher un dataset plus tard : exposer le chemin du fichier via la
variable d'environnement `DOWFLOW_MUSIC_DATASET` (base SQLite) puis retourner
une instance de `MusicDataset` depuis `get_dataset()`.
"""
from __future__ import annotations

import os
from typing import Any

DATASET_ENV_VAR = "DOWFLOW_MUSIC_DATASET"


class MusicDataset:
    """Interface d'un catalogue musical local (implémentation à fournir)."""

    def available(self) -> bool:
        """True si le catalogue est lisible."""
        return False

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Recherche de morceaux : [{title, artist, album, cover_url, ...}]."""
        raise NotImplementedError

    def popular(self, limit: int = 24) -> list[dict[str, Any]]:
        """Sélection de morceaux populaires."""
        raise NotImplementedError

    def cover_url(self, artist: str, title: str) -> str | None:
        """URL de pochette d'album pour un morceau donné, ou None."""
        return None


def get_dataset() -> MusicDataset | None:
    """Retourne le dataset local s'il est disponible, sinon None.

    Voir le docstring du module pour brancher une implémentation concrète.
    """
    dataset_path = os.environ.get(DATASET_ENV_VAR, "").strip()
    if not dataset_path or not os.path.isfile(dataset_path):
        return None
    # Aucune implémentation fournie pour l'instant : le fichier est détecté mais
    # n'est pas encore interrogeable. Retourner None garde le comportement
    # YouTube + Deezer intact.
    return None
