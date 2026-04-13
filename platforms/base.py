"""Interface commune pour toutes les plateformes de streaming."""

from dataclasses import dataclass, field


@dataclass
class TrackInfo:
    """Métadonnées d'un morceau découvert sur une plateforme."""
    title: str
    artist: str
    album: str = ""
    duration_sec: int = 0
    isrc: str = ""
    platform: str = ""
    platform_id: str = ""
    cover_url: str = ""
    language: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def search_query(self) -> str:
        """Requête optimisée pour chercher ce morceau sur YouTube."""
        return f"{self.artist} - {self.title} official audio"

    @property
    def uid(self) -> str:
        """Identifiant unique cross-plateforme (ISRC si dispo, sinon artist+title)."""
        if self.isrc:
            return self.isrc
        norm = f"{self.artist}__{self.title}".lower().strip()
        return norm


class BasePlatform:
    """Classe de base pour les plateformes de streaming."""

    name: str = "base"

    def get_artist_tracks(self, artist_name: str, limit: int = 50) -> list[TrackInfo]:
        raise NotImplementedError

    def is_available(self) -> bool:
        """Vérifie si la plateforme est configurée et accessible."""
        return True
