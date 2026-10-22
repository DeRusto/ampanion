"""On Repeat: the tracks a Listener has been playing most lately."""

from collections import Counter
from datetime import timedelta

from ampanion.mixes.rule import Mix, MixInput, Setting


def rule(mix: MixInput) -> dict[str | None, list[str]]:
    """Tracks with 2+ Plays in the window, by Plays there, ties to the latest Play. Skips are ignored."""
    since = mix.now - timedelta(days=mix.settings["window_days"])
    plays, latest = Counter(), {}
    for p in mix.plays:
        if p.played_at >= since:
            plays[p.rating_key] += 1
            latest[p.rating_key] = max(latest.get(p.rating_key, p.played_at), p.played_at)
    ranked = sorted((k for k, n in plays.items() if n >= 2), key=lambda k: (plays[k], latest[k]), reverse=True)
    tracks = mix.library.tracks
    # A track gone from the library, or rated 1 star or lower, yields to the next rank.
    return {None: [k for k in ranked if k in tracks and not mix.ratings.excludes(tracks[k])][: mix.settings["length"]]}


ON_REPEAT = Mix(
    key="on_repeat",
    title="On Repeat",
    rule=rule,
    settings=(Setting("window_days", "Window (days)", 30, max=3650), Setting("length", "Length", 30)),
    cadence="daily",
)
