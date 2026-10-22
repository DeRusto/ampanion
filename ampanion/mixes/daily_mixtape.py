"""Daily Mixtape: familiar tracks and unplayed ones by artists the Listener plays, blended into a Playlist new every day."""

from collections import Counter

from ampanion.mixes.artists import VARIOUS, pick_capped
from ampanion.mixes.rule import Mix, MixInput, Setting

RECENCY_DAYS = 30  # a familiar track last played this many days ago is half as likely as one played just now


def rule(mix: MixInput) -> dict[str | None, list[str]]:
    """Familiar: 2+ Plays, weighted toward recent Plays. Fresh: 0 Plays by a played artist. Split by fresh share, interleaved."""
    plays, latest = Counter(), {}
    for p in mix.plays:
        plays[p.rating_key] += 1
        latest[p.rating_key] = max(latest.get(p.rating_key, p.played_at), p.played_at)
    played_artists = {p.album_artist for p in mix.plays} - {VARIOUS}
    tracks, excluded = mix.library.tracks, set(mix.previous.get(None, [])) | mix.often_skipped()
    allowed = [k for k, t in tracks.items() if k not in excluded and not mix.ratings.excludes(t)]
    familiar = [k for k in allowed if plays[k] >= 2]
    fresh = [k for k in allowed if plays[k] == 0 and tracks[k].album_artist in played_artists]

    def artist_of(key: str) -> str:
        return tracks[key].album_artist

    def recency(key: str) -> float:
        return RECENCY_DAYS / (RECENCY_DAYS + (mix.now - latest[key]).total_seconds() / 86400)

    length, taken = mix.settings["length"], Counter()  # the per-artist cap spans both sides

    def pick(pool: list[str], n: int, **weight) -> list[str]:
        return pick_capped(pool, artist_of, n, mix.settings["tracks_per_artist"], mix.rng, taken=taken, **weight)

    picked = pick(fresh, round(length * mix.settings["fresh_share"] / 100))
    picked += pick(familiar, length - len(picked), weight=recency)  # also fills a short fresh side
    chosen = set(picked)
    picked += pick([k for k in fresh if k not in chosen], length - len(picked))  # fills a short familiar side
    return {None: interleave(picked, set(fresh), artist_of, mix.rng)}


def interleave(picked: list[str], fresh: set[str], artist_of, rng) -> list[str]:
    """Fresh tracks spaced evenly among familiar ones, then artists kept apart where the slots allow it."""
    sides = {True: [k for k in picked if k in fresh], False: [k for k in picked if k not in fresh]}
    for side in sides.values():
        rng.shuffle(side)  # so ties below fall at random
    n, f = len(picked), len(sides[True])
    left = Counter(map(artist_of, picked))
    ordered, last = [], None
    for i in range(n):
        side = sides[(i + 1) * f // n > i * f // n]  # Bresenham: the f fresh slots spread over n
        options = [k for k in side if artist_of(k) != last] or side
        # the artist with most tracks left goes first, so few are left to bunch up at the end
        track = max(options, key=lambda k: left[artist_of(k)])
        side.remove(track)
        last = artist_of(track)
        left[last] -= 1
        ordered.append(track)
    return ordered


DAILY_MIXTAPE = Mix(
    key="daily_mixtape",
    title="Daily Mixtape",
    rule=rule,
    settings=(
        Setting("length", "Length", 50),
        Setting("tracks_per_artist", "Tracks per artist", 2),
        Setting("fresh_share", "Fresh share (%)", 40, min=0, max=100),
    ),
    cadence="daily",
)
