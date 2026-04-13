"""Recherche YouTube et téléchargement audio via yt-dlp."""

import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("collector.downloader")


def search_youtube(query: str, max_results: int = 3) -> list[dict]:
    """Cherche une vidéo sur YouTube. Retourne les métadonnées des résultats."""
    search_query = f"ytsearch{max_results}:{query}"
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--flat-playlist",
        "--no-warnings",
        "--default-search", "auto",
        search_query,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        log.warning("Timeout recherche YouTube: %s", query)
        return []

    entries = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def pick_best_result(entries: list[dict], expected_duration: int = 0) -> dict | None:
    """Sélectionne le meilleur résultat YouTube.
    Privilégie les vidéos dont la durée est proche de celle attendue,
    et filtre les vidéos trop longues (lives, compilations).
    """
    if not entries:
        return None

    candidates = []
    for e in entries:
        dur = e.get("duration") or 0
        # Filtrer les vidéos > 10 min (probablement pas un single)
        if dur > 600:
            continue
        candidates.append(e)

    if not candidates:
        # Si tout est filtré, prendre le premier résultat brut
        return entries[0] if entries else None

    if expected_duration > 0:
        # Trier par proximité de durée
        candidates.sort(key=lambda e: abs((e.get("duration") or 0) - expected_duration))

    return candidates[0]


def download_audio(youtube_id: str, output_dir: Path) -> tuple[str, int] | None:
    """Télécharge l'audio d'une vidéo YouTube en MP3.
    Retourne (chemin_fichier, durée_sec) ou None.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(output_dir / "%(id)s.%(ext)s")
    url = f"https://www.youtube.com/watch?v={youtube_id}"
    cmd = [
        "yt-dlp",
        "-x", "--audio-format", "mp3",
        "--audio-quality", "192K",
        "--no-playlist",
        "--no-warnings",
        "--no-overwrites",
        "-o", output_template,
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        log.warning("Timeout téléchargement pour %s", youtube_id)
        return None

    if result.returncode != 0:
        log.warning("Échec téléchargement %s: %s", youtube_id, result.stderr[:300])
        return None

    # Trouver le fichier téléchargé
    candidates = list(output_dir.glob(f"{youtube_id}.*"))
    if not candidates:
        log.warning("Fichier non trouvé après téléchargement de %s", youtube_id)
        return None

    filepath = str(candidates[0])

    # Récupérer la durée via ffprobe
    duration = _get_duration(filepath)
    return filepath, duration


def _get_duration(filepath: str) -> int:
    """Récupère la durée d'un fichier audio via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                filepath,
            ],
            capture_output=True, text=True, timeout=10,
        )
        return int(float(result.stdout.strip()))
    except Exception:
        return 0
