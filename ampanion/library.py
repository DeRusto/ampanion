"""Library index and Ratings: the library-wide track facts loaded once per run, and one Listener's Ratings."""

from dataclasses import dataclass

from ampanion.gateway import Gateway, LibraryTrack

ONE_STAR = 2.0  # Plex Ratings run 0-10, two per star


@dataclass(frozen=True)
class LibraryIndex:
    tracks: dict[str, LibraryTrack]  # by rating key
    by_guid: dict[str, str]  # guid -> rating key

    @classmethod
    def load(cls, gateway: Gateway, url: str, token: str) -> "LibraryIndex":
        tracks = {t.rating_key: t for t in gateway.library_tracks(url, token)}
        return cls(tracks=tracks, by_guid={t.guid: t.rating_key for t in tracks.values() if t.guid})


@dataclass(frozen=True)
class Ratings:
    """A Listener's Ratings by rating key (tracks, albums and artists), read fresh each Refresh and never stored."""

    by_key: dict[str, float]

    def effective(self, track: LibraryTrack) -> float | None:
        """The track's Rating, else its album's, else its artist's; None (neutral) if none is rated."""
        return next((self.by_key[k] for k in (track.rating_key, track.album_key, track.artist_key) if k in self.by_key), None)

    def excludes(self, track: LibraryTrack) -> bool:
        """1 star or lower keeps a track off every Playlist."""
        rating = self.effective(track)
        return rating is not None and rating <= ONE_STAR
