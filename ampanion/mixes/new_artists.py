"""New Artists: tracks by artists in the library the Listener has never played, favouring their Top Genres."""

from ampanion.library import ONE_STAR
from ampanion.mixes.artists import VARIOUS, spread_shuffle
from ampanion.mixes.genre_mix import GENRE_MIX, top_genres
from ampanion.mixes.rule import Mix, MixInput, Setting

TOP_GENRES = next(s.default for s in GENRE_MIX.settings if s.key == "count")  # the same Top Genres Genre Mix makes by default
FAVOURED = 4  # an artist sharing a Top Genre is this many times likelier to be picked


def rule(mix: MixInput) -> dict[str | None, list[str]]:
    """0-Play album artists not often skipped, weighted toward Top Genres; per artist the most popular tracks first; capped and spread."""
    tracks = mix.library.tracks
    played = {p.album_artist for p in mix.plays}
    last_time = {tracks[k].album_artist for k in mix.previous.get(None, []) if k in tracks}  # skipped for one Refresh
    excluded = played | last_time | mix.often_skipped(by=lambda s: s.album_artist) | {VARIOUS}
    by_artist: dict[str, list[str]] = {}
    for k, t in tracks.items():
        # an artist rated 1 star or lower is dropped even where a track's own Rating would let it through
        if t.album_artist not in excluded and mix.ratings.by_key.get(t.artist_key, ONE_STAR + 1) > ONE_STAR and not mix.ratings.excludes(t):
            by_artist.setdefault(t.album_artist, []).append(k)

    top = set(top_genres(mix, TOP_GENRES))

    def weight(artist: str) -> float:
        return FAVOURED if any(top & set(tracks[k].genres) for k in by_artist[artist]) else 1

    # exponential race: sorting by Exp(weight) draws is a weighted order without replacement
    artists = sorted(by_artist, key=lambda a: mix.rng.expovariate(weight(a)))
    picked, length, per_artist = [], mix.settings["length"], mix.settings["tracks_per_artist"]
    for artist in artists:
        pool = by_artist[artist]
        mix.rng.shuffle(pool)  # random among tracks with no popularity, which sort last
        pool.sort(key=lambda k: -(tracks[k].popularity or 0))
        picked += pool[: min(per_artist, length - len(picked))]
        if len(picked) == length:
            break
    return {None: spread_shuffle(picked, lambda k: tracks[k].album_artist, mix.rng)}


NEW_ARTISTS = Mix(
    key="new_artists",
    title="New Artists",
    rule=rule,
    settings=(Setting("length", "Length", 30), Setting("tracks_per_artist", "Tracks per artist", 3)),
)
