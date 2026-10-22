"""Deep Cuts: tracks a Listener rarely or never plays by their Top Artists."""

from collections import Counter

from ampanion.mixes.artists import pick_capped, spread_shuffle, top_artists
from ampanion.mixes.rule import Mix, MixInput, Setting


def rule(mix: MixInput) -> dict[str | None, list[str]]:
    """Library tracks by a Top Artist with at most 1 Play ever, not often skipped, picked at random, capped and spread."""
    top, plays, tracks = set(top_artists(mix)), Counter(p.rating_key for p in mix.plays), mix.library.tracks
    skipped = mix.often_skipped()
    cuts = [
        k for k, t in tracks.items()
        if t.album_artist in top and plays[k] <= 1 and k not in skipped and not mix.ratings.excludes(t)
    ]

    def artist_of(key: str) -> str:
        return tracks[key].album_artist

    picked = pick_capped(cuts, artist_of, mix.settings["length"], mix.settings["tracks_per_artist"], mix.rng)
    return {None: spread_shuffle(picked, artist_of, mix.rng)}


DEEP_CUTS = Mix(
    key="deep_cuts",
    title="Deep Cuts",
    rule=rule,
    settings=(Setting("length", "Length", 30), Setting("tracks_per_artist", "Tracks per artist", 3)),
)
