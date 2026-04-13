"""Découverte de morceaux via l'API MusicBrainz (gratuite, sans auth)."""

import logging
import time

import requests

from .base import BasePlatform, TrackInfo

log = logging.getLogger("collector.musicbrainz")

API_BASE = "https://musicbrainz.org/ws/2"
HEADERS = {
    "User-Agent": "KaraokingDatasetCollector/1.0 (contact@karaoking.app)",
    "Accept": "application/json",
}
# MusicBrainz impose 1 req/sec max
RATE_LIMIT = 1.1


class MusicBrainzPlatform(BasePlatform):
    name = "musicbrainz"

    def _search_artist(self, artist_name: str) -> str | None:
        """Retourne le MBID de l'artiste."""
        try:
            resp = requests.get(
                f"{API_BASE}/artist",
                params={"query": artist_name, "limit": 5, "fmt": "json"},
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            artists = resp.json().get("artists", [])
            if not artists:
                return None
            # Prendre celui avec le meilleur score
            return artists[0].get("id")
        except Exception as e:
            log.warning("MusicBrainz recherche artiste '%s' échouée : %s", artist_name, e)
            return None

    def _get_recordings(self, artist_mbid: str, limit: int = 100, offset: int = 0) -> list[dict]:
        try:
            time.sleep(RATE_LIMIT)
            resp = requests.get(
                f"{API_BASE}/recording",
                params={
                    "artist": artist_mbid,
                    "limit": min(limit, 100),
                    "offset": offset,
                    "fmt": "json",
                },
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("recordings", [])
        except Exception as e:
            log.warning("MusicBrainz recordings échoué : %s", e)
            return []

    def get_artist_tracks(self, artist_name: str, limit: int = 50) -> list[TrackInfo]:
        artist_mbid = self._search_artist(artist_name)
        if not artist_mbid:
            log.info("MusicBrainz: artiste '%s' non trouvé", artist_name)
            return []

        time.sleep(RATE_LIMIT)

        tracks_map: dict[str, TrackInfo] = {}
        offset = 0
        max_pages = 3  # Limiter les pages pour ne pas abuser de l'API

        for _ in range(max_pages):
            if len(tracks_map) >= limit:
                break
            recordings = self._get_recordings(artist_mbid, limit=100, offset=offset)
            if not recordings:
                break
            for rec in recordings:
                if len(tracks_map) >= limit:
                    break
                info = self._to_track_info(rec, artist_name)
                if info:
                    tracks_map.setdefault(info.uid, info)
            offset += 100

        result = list(tracks_map.values())[:limit]
        log.info("MusicBrainz: %d morceaux trouvés pour '%s'", len(result), artist_name)
        return result

    def _to_track_info(self, rec: dict, artist_name: str) -> TrackInfo | None:
        title = rec.get("title", "").strip()
        if not title:
            return None
        duration_ms = rec.get("length") or 0
        # Extraire l'ISRC s'il y en a
        isrc = ""
        isrcs = rec.get("isrcs", [])
        if isrcs:
            isrc = isrcs[0]
        return TrackInfo(
            title=title,
            artist=artist_name,
            duration_sec=duration_ms // 1000,
            isrc=isrc,
            platform="musicbrainz",
            platform_id=rec.get("id", ""),
        )
