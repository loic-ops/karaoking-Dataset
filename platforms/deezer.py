"""Découverte de morceaux via l'API Deezer (gratuite, sans auth)."""

import logging
import time

import requests

from .base import BasePlatform, TrackInfo

log = logging.getLogger("collector.deezer")

API_BASE = "https://api.deezer.com"


class DeezerPlatform(BasePlatform):
    name = "deezer"

    def _find_artist_id(self, artist_name: str) -> int | None:
        try:
            resp = requests.get(
                f"{API_BASE}/search/artist",
                params={"q": artist_name, "limit": 5},
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("data", [])
            if not items:
                return None
            return items[0]["id"]
        except Exception as e:
            log.warning("Deezer recherche artiste '%s' échouée : %s", artist_name, e)
            return None

    def _get_artist_top(self, artist_id: int, limit: int = 50) -> list[dict]:
        try:
            resp = requests.get(
                f"{API_BASE}/artist/{artist_id}/top",
                params={"limit": min(limit, 100)},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            log.warning("Deezer top tracks échoué : %s", e)
            return []

    def _get_albums(self, artist_id: int, limit: int = 30) -> list[dict]:
        try:
            resp = requests.get(
                f"{API_BASE}/artist/{artist_id}/albums",
                params={"limit": min(limit, 100)},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            log.warning("Deezer albums échoué : %s", e)
            return []

    def _get_album_tracks(self, album_id: int) -> list[dict]:
        try:
            resp = requests.get(
                f"{API_BASE}/album/{album_id}/tracks",
                params={"limit": 100},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            log.warning("Deezer album tracks échoué : %s", e)
            return []

    def get_artist_tracks(self, artist_name: str, limit: int = 50) -> list[TrackInfo]:
        artist_id = self._find_artist_id(artist_name)
        if not artist_id:
            log.info("Deezer: artiste '%s' non trouvé", artist_name)
            return []

        tracks_map: dict[str, TrackInfo] = {}

        # 1. Top tracks
        for t in self._get_artist_top(artist_id, limit):
            info = self._to_track_info(t, artist_name)
            if info:
                tracks_map[info.uid] = info

        # 2. Albums pour compléter
        if len(tracks_map) < limit:
            albums = self._get_albums(artist_id)
            for album in albums:
                if len(tracks_map) >= limit:
                    break
                album_tracks = self._get_album_tracks(album["id"])
                for t in album_tracks:
                    if len(tracks_map) >= limit:
                        break
                    info = self._to_track_info(t, artist_name, album_title=album.get("title", ""))
                    if info:
                        tracks_map.setdefault(info.uid, info)
                time.sleep(0.3)

        result = list(tracks_map.values())[:limit]
        log.info("Deezer: %d morceaux trouvés pour '%s'", len(result), artist_name)
        return result

    def _to_track_info(self, t: dict, artist_name: str, album_title: str = "") -> TrackInfo | None:
        title = t.get("title", "").strip()
        if not title:
            return None
        album = album_title or t.get("album", {}).get("title", "")
        isrc = t.get("isrc", "")
        return TrackInfo(
            title=title,
            artist=artist_name,
            album=album,
            duration_sec=t.get("duration", 0),
            isrc=isrc,
            platform="deezer",
            platform_id=str(t.get("id", "")),
        )
