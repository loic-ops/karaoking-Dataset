"""
Collecteur de musique pour le dataset karaoké.

Pipeline :
1. Parcourt Spotify, Deezer, MusicBrainz pour découvrir les morceaux de chaque artiste
2. Dédoublonne par ISRC ou titre normalisé
3. Cherche chaque morceau sur YouTube et télécharge l'audio (yt-dlp)
4. Récupère les paroles synchronisées (LRCLib)
5. Insère dans la DB MySQL de l'app karaoké (tables songs + artists)
"""

import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path

import pymysql
from sqlalchemy import create_engine, text

from platforms import ALL_PLATFORMS
from platforms.base import TrackInfo
from platforms.lrclib import fetch_synced_lyrics
from downloader import search_youtube, pick_best_result, download_audio

# --- Configuration ---
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "karaoke")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "karaoke_password")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "karaoke_db")
SONGS_PER_ARTIST = int(os.getenv("SONGS_PER_ARTIST", "50"))
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", "/app/static/uploads"))
ARTISTS_FILE = Path(os.getenv("ARTISTS_FILE", "/app/artists.json"))

# --- Logging ---
os.makedirs("/app/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/logs/collector.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("collector")

# --- DB ---
DB_URL = (
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
)


def slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[''`]", "", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


# --- DB helpers ---

def wait_for_db(engine, retries=30, delay=2):
    for attempt in range(retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            log.info("Connecté à MySQL.")
            return
        except Exception:
            log.info("MySQL pas encore prêt, tentative %d/%d...", attempt + 1, retries)
            time.sleep(delay)
    log.error("Impossible de se connecter à MySQL.")
    sys.exit(1)


def get_or_create_artist(engine, name: str) -> int:
    """Retourne l'id de l'artiste, le crée s'il n'existe pas."""
    slug = slugify(name)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM artists WHERE slug = :slug"),
            {"slug": slug},
        ).fetchone()
        if row:
            return row[0]

        conn.execute(
            text(
                "INSERT INTO artists (name, slug, songs_count) "
                "VALUES (:name, :slug, 0)"
            ),
            {"name": name, "slug": slug},
        )
        conn.commit()
        row = conn.execute(
            text("SELECT id FROM artists WHERE slug = :slug"),
            {"slug": slug},
        ).fetchone()
        log.info("  Artiste créé: %s (id=%d)", name, row[0])
        return row[0]


def song_exists_by_source_url(engine, source_url: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM songs WHERE source_url = :url"),
            {"url": source_url},
        ).fetchone()
    return row is not None


def song_exists_by_title_artist(engine, title: str, artist_id: int) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM songs WHERE title = :title AND artist_id = :aid LIMIT 1"),
            {"title": title, "aid": artist_id},
        ).fetchone()
    return row is not None


def insert_song(engine, song_id: str, title: str, original_file: str,
                artist_id: int, source_url: str, duration_sec: float,
                lyrics: str | None, language: str, album: str,
                cover_path: str, country: str):
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO songs "
                "(id, title, original_file, status, source, source_url, "
                " artist_id, duration_sec, lyrics_file, language, album, "
                " cover_path) "
                "VALUES (:id, :title, :file, 'uploaded', 'youtube', :url, "
                " :aid, :dur, :lyrics, :lang, :album, :cover)"
            ),
            {
                "id": song_id,
                "title": title,
                "file": original_file,
                "url": source_url,
                "aid": artist_id,
                "dur": duration_sec,
                "lyrics": lyrics,
                "lang": language,
                "album": album,
                "cover": cover_path,
            },
        )
        # Incrémenter le compteur de songs de l'artiste
        conn.execute(
            text("UPDATE artists SET songs_count = songs_count + 1 WHERE id = :aid"),
            {"aid": artist_id},
        )
        conn.commit()


# --- Pipeline plateformes ---

def discover_tracks(artist_name: str, limit: int) -> list[TrackInfo]:
    """Interroge toutes les plateformes et dédoublonne les résultats."""
    all_tracks: dict[str, TrackInfo] = {}

    for PlatformClass in ALL_PLATFORMS:
        platform = PlatformClass()
        if not platform.is_available():
            log.info("  [%s] non disponible, skip", platform.name)
            continue

        try:
            tracks = platform.get_artist_tracks(artist_name, limit=limit)
            for t in tracks:
                # Dédoublonnage par uid (ISRC si dispo, sinon artist+title)
                all_tracks.setdefault(t.uid, t)
        except Exception as e:
            log.error("  [%s] erreur : %s", platform.name, e)

    log.info("  %d morceaux uniques découverts (toutes plateformes)", len(all_tracks))
    return list(all_tracks.values())[:limit]


# --- Pipeline principal ---

def process_track(engine, track: TrackInfo, artist_id: int, country: str) -> bool:
    """Traite un morceau : YouTube → download → lyrics → DB. Retourne True si ajouté."""

    # Vérifier si déjà en base par titre + artiste
    if song_exists_by_title_artist(engine, track.title, artist_id):
        log.info("    [skip] '%s' déjà en base", track.title)
        return False

    # Chercher sur YouTube
    yt_results = search_youtube(track.search_query, max_results=3)
    best = pick_best_result(yt_results, expected_duration=track.duration_sec)
    if not best:
        log.warning("    [miss] '%s' pas trouvé sur YouTube", track.title)
        return False

    yt_id = best.get("id") or best.get("url", "").split("=")[-1]
    if not yt_id:
        return False

    source_url = f"https://www.youtube.com/watch?v={yt_id}"

    # Vérifier par URL YouTube
    if song_exists_by_source_url(engine, source_url):
        log.info("    [skip] '%s' (YouTube %s) déjà en base", track.title, yt_id)
        return False

    # Télécharger l'audio
    artist_dir = UPLOADS_DIR / slugify(track.artist)
    result = download_audio(yt_id, artist_dir)
    if result is None:
        log.warning("    [fail] téléchargement échoué pour '%s'", track.title)
        return False

    filepath, duration = result
    # Chemin relatif depuis uploads/
    rel_path = str(Path(filepath).relative_to(UPLOADS_DIR))

    # Récupérer les paroles (LRCLib)
    lyrics = fetch_synced_lyrics(track)
    if lyrics:
        log.info("    [lrc] paroles trouvées pour '%s'", track.title)
    else:
        log.info("    [lrc] pas de paroles pour '%s'", track.title)

    # Insérer en base
    song_id = str(uuid.uuid4())
    insert_song(
        engine,
        song_id=song_id,
        title=track.title,
        original_file=rel_path,
        artist_id=artist_id,
        source_url=source_url,
        duration_sec=duration or track.duration_sec,
        lyrics=lyrics,
        language=track.language,
        album=track.album,
        cover_path=track.cover_url,
        country=country,
    )
    log.info("    [ok] '%s' ajouté (%ds)", track.title, duration or track.duration_sec)
    return True


def collect(engine):
    with open(ARTISTS_FILE, encoding="utf-8") as f:
        artists_by_country: dict[str, list[str]] = json.load(f)

    total_artists = sum(len(v) for v in artists_by_country.values())
    log.info(
        "=== Démarrage collecte : %d artistes, %d pays, %d chansons/artiste ===",
        total_artists, len(artists_by_country), SONGS_PER_ARTIST,
    )

    stats = {"downloaded": 0, "skipped": 0, "failed": 0, "no_results": 0}
    processed_artists = 0

    for country, artists in artists_by_country.items():
        log.info("--- Pays : %s (%d artistes) ---", country, len(artists))

        for artist_name in artists:
            processed_artists += 1
            log.info(
                "[%d/%d] %s (%s)",
                processed_artists, total_artists, artist_name, country,
            )

            # Créer l'artiste dans la DB si besoin
            artist_id = get_or_create_artist(engine, artist_name)

            # Découverte multi-plateforme
            tracks = discover_tracks(artist_name, SONGS_PER_ARTIST)
            if not tracks:
                log.warning("  Aucun morceau trouvé pour %s", artist_name)
                stats["no_results"] += 1
                continue

            for track in tracks:
                try:
                    added = process_track(engine, track, artist_id, country)
                    if added:
                        stats["downloaded"] += 1
                    else:
                        stats["skipped"] += 1
                except Exception as e:
                    log.error("    [err] %s : %s", track.title, e)
                    stats["failed"] += 1

                # Pause entre les morceaux
                time.sleep(2)

    log.info("=== Collecte terminée ===")
    log.info(
        "Téléchargés: %d | Ignorés: %d | Échoués: %d | Sans résultats: %d",
        stats["downloaded"], stats["skipped"], stats["failed"], stats["no_results"],
    )


def main():
    log.info("Connexion à MySQL %s:%s ...", MYSQL_HOST, MYSQL_PORT)
    engine = create_engine(DB_URL, pool_pre_ping=True)
    wait_for_db(engine)
    collect(engine)


if __name__ == "__main__":
    main()
