"""Artist helpers every Mix past On Repeat shares: Top Artists, a random pick capped per artist, and the spreading shuffle.

"Artist" means the album artist throughout.
"""

from collections import Counter
from collections.abc import Callable
from random import Random

from ampanion.library import ONE_STAR
from ampanion.mixes.rule import MixInput

VARIOUS = "Various Artists"  # never counts as an artist
TOP_ARTISTS, TOP_ARTIST_PLAYS = 15, 10


def top_artists(mix: MixInput) -> list[str]:
    """The top 15 by all-time Plays with at least 10; if fewer qualify, the top 15 with any Plays. Ties to the latest Play.

    Artists rated 1 star or lower never qualify.
    """
    plays, latest = Counter(), {}
    for p in mix.plays:
        plays[p.album_artist] += 1
        latest[p.album_artist] = max(latest.get(p.album_artist, p.played_at), p.played_at)
    keys = {t.album_artist: t.artist_key for t in mix.library.tracks.values()}

    def low_rated(artist: str) -> bool:
        rating = mix.ratings.by_key.get(keys.get(artist))
        return rating is not None and rating <= ONE_STAR

    ranked = sorted(
        (a for a in plays if a != VARIOUS and not low_rated(a)), key=lambda a: (plays[a], latest[a]), reverse=True
    )
    qualifying = [a for a in ranked if plays[a] >= TOP_ARTIST_PLAYS]
    return (qualifying if len(qualifying) >= TOP_ARTISTS else ranked)[:TOP_ARTISTS]


def pick_capped(tracks: list[str], artist_of: Callable[[str], str], length: int, per_artist: int, rng: Random) -> list[str]:
    """A random pick of up to `length` tracks, at most `per_artist` by any one artist."""
    pool, taken, picked = list(tracks), Counter(), []
    rng.shuffle(pool)
    for t in pool:
        if len(picked) == length:
            break
        if taken[artist_of(t)] < per_artist:
            taken[artist_of(t)] += 1
            picked.append(t)
    return picked


def spread_shuffle(tracks: list[str], artist_of: Callable[[str], str], rng: Random) -> list[str]:
    """Random order with no two tracks by the same artist side by side, where the mix of artists allows it."""
    left: dict[str, list[str]] = {}
    for t in tracks:
        left.setdefault(artist_of(t), []).append(t)
    for group in left.values():
        rng.shuffle(group)

    def safe_after(artist: str) -> bool:
        """Whether, once `artist` takes the next slot, the rest can still be kept apart."""
        n = sum(map(len, left.values())) - 1
        # n tracks can follow `artist` iff none has over half of them, and `artist` (not allowed first) no more than n // 2.
        return all(len(g) - (a == artist) <= (n // 2 if a == artist else (n + 1) // 2) for a, g in left.items())

    ordered, last = [], None
    while left:
        options = [a for a in left if a != last]
        safe = [a for a in options if safe_after(a)]
        if safe:  # weighted by tracks left, so every order stays possible
            artist = rng.choices(safe, weights=[len(left[a]) for a in safe])[0]
        else:  # can't be kept apart: spend the biggest artist first, to need as few neighbours alike as possible
            artist = max(options or left, key=lambda a: len(left[a]))
        ordered.append(left[artist].pop())
        if not left[artist]:
            del left[artist]
        last = artist
    return ordered
