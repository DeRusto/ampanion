"""Genre Mix: one Playlist per Top Genre, a random pick from the library's tracks in that genre."""

from collections import Counter

from ampanion.mixes.artists import pick_capped, spread_shuffle
from ampanion.mixes.rule import Mix, MixInput, Setting

TOP_GENRE_PLAYS = 20


def top_genres(mix: MixInput, count: int) -> list[str]:
    """Genres by all-time Plays (a track counts toward each of its genres), at least 20, ties to the latest Play."""
    plays, latest, tracks = Counter(), {}, mix.library.tracks
    for p in mix.plays:
        for g in tracks[p.rating_key].genres if p.rating_key in tracks else ():
            plays[g] += 1
            latest[g] = max(latest.get(g, p.played_at), p.played_at)
    ranked = sorted((g for g, n in plays.items() if n >= TOP_GENRE_PLAYS), key=lambda g: (plays[g], latest[g]), reverse=True)
    return ranked[:count]


def rule(mix: MixInput) -> dict[str | None, list[str]]:
    tracks, skipped = mix.library.tracks, mix.often_skipped()

    def artist_of(key: str) -> str:
        return tracks[key].album_artist

    playlists = {}
    for genre in top_genres(mix, mix.settings["count"]):
        pool = [k for k, t in tracks.items() if genre in t.genres and k not in skipped and not mix.ratings.excludes(t)]
        picked = pick_capped(pool, artist_of, mix.settings["length"], mix.settings["tracks_per_artist"], mix.rng)
        playlists[genre] = spread_shuffle(picked, artist_of, mix.rng)
    return playlists


GENRE_MIX = Mix(
    key="genre_mix",
    title="Genre Mix",
    rule=rule,
    settings=(
        Setting("count", "Playlists", 3, max=20),
        Setting("length", "Length", 50),
        Setting("tracks_per_artist", "Tracks per artist", 3),
    ),
)
