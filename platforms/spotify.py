"""Découverte de morceaux via l'API Spotify (Client Credentials)."""

import logging
import os
import time

import requests

from .base import BasePlatform, TrackInfo

log = logging.getLogger("collector.spotify")

TOKEN_URL = "https://accounts.spotify.com/api/token"
SEARCH_URL = "https://api.spotify.com/v1/search"
ARTIST_URL = "https://api.spotify.com/v1/artists"


class SpotifyPlatform(BasePlatform):
    name = "spotify"

    def __init__(self):
        self.client_id = os.getenv("SPOTIFY_CLIENT_ID", "")
        self.client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "")
        self._token = None
        self._token_expires = 0

    def is_available(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _get_token(self) -> str | None:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        try:
            resp = requests.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._token_expires = time.time() + data["expires_in"]
            return self._token
        except Exception as e:
            log.error("Spotify auth échouée : %s", e)
            return None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    def _find_artist_id(self, artist_name: str) -> str | None:
        """Cherche l'ID Spotify d'un artiste."""
        try:
            resp = requests.get(
                SEARCH_URL,
                params={"q": artist_name, "type": "artist", "limit": 5},
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("artists", {}).get("items", [])
            if not items:
                return None
            # Prendre le meilleur match par popularité
            best = max(items, key=lambda x: x.get("popularity", 0))
            return best["id"]
        except Exception as e:
            log.warning("Spotify recherche artiste '%s' échouée : %s", artist_name, e)
            return None

    def _get_top_tracks(self, artist_id: str) -> list[dict]:
        """Récupère les top tracks d'un artiste."""
        try:
            resp = requests.get(
                f"{ARTIST_URL}/{artist_id}/top-tracks",
                params={"market": "FR"},
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("tracks", [])
        except Exception as e:
            log.warning("Spotify top tracks échouée : %s", e)
            return []

    def _get_albums(self, artist_id: str, limit: int = 50) -> list[dict]:
        """Récupère les albums d'un artiste."""
        albums = []
        url = f"{ARTIST_URL}/{artist_id}/albums"
        params = {
            "include_groups": "album,single",
            "market": "FR",
            "limit": min(limit, 50),
        }
        try:
            while url and len(albums) < limit:
                resp = requests.get(url, params=params, headers=self._headers(), timeout=10)
                resp.raise_for_status()
                data = resp.json()
                albums.extend(data.get("items", []))
                url = data.get("next")
                params = {}  # next URL inclut les params
                time.sleep(0.2)
        except Exception as e:
            log.warning("Spotify albums échoué : %s", e)
        return albums[:limit]

    def _get_album_tracks(self, album_id: str) -> list[dict]:
        """Récupère les morceaux d'un album."""
        try:
            resp = requests.get(
                f"https://api.spotify.com/v1/albums/{album_id}/tracks",
                params={"limit": 50},
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("items", [])
        except Exception as e:
            log.warning("Spotify album tracks échoué : %s", e)
            return []

    def get_artist_tracks(self, artist_name: str, limit: int = 50) -> list[TrackInfo]:
        if not self.is_available():
            return []

        artist_id = self._find_artist_id(artist_name)
        if not artist_id:
            log.info("Spotify: artiste '%s' non trouvé", artist_name)
            return []

        tracks_map: dict[str, TrackInfo] = {}

        # 1. Top tracks
        for t in self._get_top_tracks(artist_id):
            info = self._track_to_info(t, artist_name)
            if info:
                tracks_map[info.uid] = info

        # 2. Parcourir les albums pour plus de morceaux
        if len(tracks_map) < limit:
            albums = self._get_albums(artist_id, limit=20)
            for album in albums:
                if len(tracks_map) >= limit:
                    break
                album_tracks = self._get_album_tracks(album["id"])
                for t in album_tracks:
                    if len(tracks_map) >= limit:
                        break
                    info = self._album_track_to_info(t, artist_name, album)
                    if info:
                        tracks_map.setdefault(info.uid, info)
                time.sleep(0.2)

        result = list(tracks_map.values())[:limit]
        log.info("Spotify: %d morceaux trouvés pour '%s'", len(result), artist_name)
        return result

    def _track_to_info(self, t: dict, artist_name: str) -> TrackInfo | None:
        title = t.get("name", "").strip()
        if not title:
            return None
        album_data = t.get("album", {})
        isrc = t.get("external_ids", {}).get("isrc", "")
        cover = ""
        images = album_data.get("images", [])
        if images:
            cover = images[0].get("url", "")
        return TrackInfo(
            title=title,
            artist=artist_name,
            album=album_data.get("name", ""),
            duration_sec=t.get("duration_ms", 0) // 1000,
            isrc=isrc,
            platform="spotify",
            platform_id=t.get("id", ""),
            cover_url=cover,
        )

    def _album_track_to_info(self, t: dict, artist_name: str, album: dict) -> TrackInfo | None:
        title = t.get("name", "").strip()
        if not title:
            return None
        cover = ""
        images = album.get("images", [])
        if images:
            cover = images[0].get("url", "")
        return TrackInfo(
            title=title,
            artist=artist_name,
            album=album.get("name", ""),
            duration_sec=t.get("duration_ms", 0) // 1000,
            platform="spotify",
            platform_id=t.get("id", ""),
            cover_url=cover,
        )
