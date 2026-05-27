"""Home-automation predicates built from generic trusted effect facts.

These are the everyday-stakes counterpart to the lab example: thermostats,
door locks, and grocery orders. The relation primitives are the same — value
allowlists, numeric aggregation, per-fact bounds — applied to a different
effect vocabulary.
"""

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

from .effects import DoorLocked

Thermostat = effect("set_thermostat")
Unlock = effect("unlock_door")
Grocery = effect("order_grocery")


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about home effects."""
    return core_no_guarantees()


# ── Thermostat ────────────────────────────────────────────────────────────────


@contract
def temp_between(min_f: int, max_f: int) -> ContractSpec:
    """Guarantee every thermostat set is within ``[min_f, max_f]`` Fahrenheit."""
    return Thermostat.all(lambda e: e.temp_f >= min_f and e.temp_f <= max_f)


@contract
def only_zones(allowed: list[str]) -> ContractSpec:
    """Guarantee every thermostat set targets a zone in ``allowed``."""
    return Thermostat.all(lambda e: e.zone in allowed)


# ── Door locks ───────────────────────────────────────────────────────────────


@contract
def no_unlocks() -> ContractSpec:
    """Guarantee no door is unlocked."""
    return Unlock.empty()


@contract
def only_unlock_doors(allowed: list[str]) -> ContractSpec:
    """Guarantee any door unlocked is in ``allowed``."""
    return Unlock.all(lambda e: e.door in allowed)


@contract
def never_unlock_door(door: str) -> ContractSpec:
    """Guarantee ``door`` is never unlocked.

    Distinct from ``only_unlock_doors``: this lets a user veto one specific
    door without enumerating an allowlist of the others.
    """
    return Unlock.where(lambda e: e.door == door).empty()


# ── Door state at end (fluents) ──────────────────────────────────────────────
#
# Unlike ``no_unlocks`` / ``never_unlock_door`` above, which forbid the unlock
# *event* anywhere in the trace, these guarantee the final *state*: a door may
# be unlocked mid-program as long as it is re-locked before the program ends.


@contract
def all_doors_locked_at_end() -> ContractSpec:
    """Guarantee every door is locked once the program finishes.

    A door may be unlocked partway through (to enter, fetch something, leave),
    as long as a later ``lock_door`` call restores it before the end.
    """
    return DoorLocked.final.all(lambda d: d.value == True)  # noqa: E712


@contract
def door_locked_at_end(door: str) -> ContractSpec:
    """Guarantee ``door`` is locked once the program finishes."""
    return DoorLocked.final[door] == True  # noqa: E712


# ── Groceries ────────────────────────────────────────────────────────────────


@contract
def grocery_budget(max_usd: int) -> ContractSpec:
    """Guarantee total grocery spend is at most ``max_usd``."""
    return Grocery.sum(lambda e: e.usd) <= max_usd


@contract
def grocery_items_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` grocery line items are ordered."""
    return Grocery.count() <= count


@contract
def no_groceries() -> ContractSpec:
    """Guarantee no grocery order is placed."""
    return Grocery.empty()
