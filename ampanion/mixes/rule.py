"""The shape every Mix rule shares: what a rule is given and what a Mix declares."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from random import Random

from ampanion.library import LibraryIndex, Ratings
from ampanion.store import Play, Skip

OFTEN_SKIPPED = 2  # Skips that make a track (or, for New Artists, an artist) often skipped


@dataclass(frozen=True)
class MixInput:
    """Everything a rule may use. Rules are pure: same input (and random state), same tracks."""

    plays: list[Play]  # the Listener's whole cached history; a Play whose track isn't in `library` counts for taste only
    skips: list[Skip]  # the Listener's cached Skips; empty when Tautulli isn't configured
    library: LibraryIndex
    ratings: Ratings
    settings: dict[str, int]  # this Listener's values of the Mix's settings, keyed by Setting.key
    previous: dict[str | None, list[str]]  # last published rating keys per Playlist (keyed like the result)
    now: datetime
    rng: Random

    def often_skipped(self, by=lambda s: s.rating_key) -> set[str]:
        """Rating keys (or whatever `by` names, e.g. album artists) with 2+ Skips in total."""
        counts = Counter(map(by, self.skips))
        return {k for k, n in counts.items() if n >= OFTEN_SKIPPED}


# A rule returns the ordered rating keys of each Playlist: {None: [...]} for one Playlist, {genre: [...]} for several.
Rule = Callable[[MixInput], dict[str | None, list[str]]]


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    default: int
    min: int = 1
    max: int = 1000


@dataclass(frozen=True)
class Mix:
    key: str  # used in URLs and the store
    title: str  # the Mix's name, and its Playlist's title
    rule: Rule
    settings: tuple[Setting, ...]  # every Mix has a "length" setting, which status lines compare against
    cadence: str = "weekly"  # the default server-wide cadence, "daily" or "weekly" (on Mondays)
