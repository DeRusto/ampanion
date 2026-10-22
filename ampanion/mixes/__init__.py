"""Mixes: the registry of every Mix, and each Listener's settings for them. Each Mix lives in its own module."""

from ampanion.mixes.rule import Mix, MixInput, Rule, Setting
from ampanion.mixes.daily_mixtape import DAILY_MIXTAPE
from ampanion.mixes.deep_cuts import DEEP_CUTS
from ampanion.mixes.genre_mix import GENRE_MIX
from ampanion.mixes.new_artists import NEW_ARTISTS
from ampanion.mixes.on_repeat import ON_REPEAT
from ampanion.store import Store

# Add a Mix by writing its module and listing it here; the runner and the Mixes tab pick it up.
MIXES: dict[str, Mix] = {m.key: m for m in (ON_REPEAT, DEEP_CUTS, GENRE_MIX, DAILY_MIXTAPE, NEW_ARTISTS)}


def mix_config(store: Store, account_id: int, mix: Mix) -> tuple[bool, dict[str, int]]:
    """(enabled, settings) for the Listener, defaults filling anything never saved. Mixes start enabled."""
    enabled, saved = store.mix_settings(account_id, mix.key) or (True, {})
    return enabled, {s.key: saved.get(s.key, s.default) for s in mix.settings}


__all__ = ["MIXES", "Mix", "MixInput", "Rule", "Setting", "mix_config"]
