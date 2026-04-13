"""Récupération de paroles synchronisées via LRCLIB (gratuit, sans auth)."""

import logging
import time

import requests

from .base import TrackInfo

log = logging.getLogger("collector.lrclib")

API_BASE = "https://lrclib.net/api"
HEADERS = {
    "User-Agent": "KaraokingDatasetCollector/1.0 (contact@karaoking.app)",
}


def fetch_synced_lyrics(track: TrackInfo) -> str | None:
    """Cherche les paroles synchronisées (LRC) pour un morceau.
    Retourne le texte LRC ou None.
    """
    # 1. Essai exact par titre + artiste + durée
    lyrics = _get_exact(track)
    if lyrics:
        return lyrics

    # 2. Fallback recherche textuelle
    return _search(track)


def _get_exact(track: TrackInfo) -> str | None:
    params = {
        "track_name": track.title,
        "artist_name": track.artist,
    }
    if track.duration_sec > 0:
        params["duration"] = track.duration_sec
    if track.album:
        params["album_name"] = track.album

    try:
        resp = requests.get(
            f"{API_BASE}/get",
            params=params,
            headers=HEADERS,
            timeout=10,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        # Préférer les lyrics synchronisées, sinon plain
        synced = data.get("syncedLyrics")
        if synced:
            return synced
        return data.get("plainLyrics")
    except Exception as e:
        log.debug("LRCLib get exact échoué pour '%s - %s': %s", track.artist, track.title, e)
        return None


def _search(track: TrackInfo) -> str | None:
    try:
        time.sleep(0.3)
        resp = requests.get(
            f"{API_BASE}/search",
            params={
                "q": f"{track.artist} {track.title}",
                "limit": 5,
            },
            headers=HEADERS,
            timeout=10,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None

        # Prendre le premier résultat avec des lyrics synchronisées
        for item in results:
            synced = item.get("syncedLyrics")
            if synced:
                return synced

        # Sinon, prendre les plain lyrics du premier résultat
        plain = results[0].get("plainLyrics")
        return plain
    except Exception as e:
        log.debug("LRCLib search échoué pour '%s - %s': %s", track.artist, track.title, e)
        return None
