import deal

from clauz3.fluent import effect, fluent

# Successor-state fluent: every door starts locked; ``unlock_door`` clears the
# bit and ``lock_door`` sets it. Contracts in ``contracts.py`` query the final
# valuation (e.g. "all doors locked at end"). Because the per-call effect bits
# below live in the trusted layer, the *trusted* layer enforces them — the
# agent cannot lie about having re-locked a door the way an honor-system
# ``end_session(front_locked)`` precondition would allow.
DoorLocked = fluent("door_locked", key=str, value=bool, initial=True)


@deal.pre(
    lambda zone, temp_f: 30 <= temp_f <= 90,
    message="temp_f must be a sane indoor range [30, 90]",
)
@deal.has("trusted")
def set_thermostat(zone: str, temp_f: int) -> None:
    """MOCK trusted thermostat setpoint.

    Sets ``zone`` thermostat to ``temp_f`` Fahrenheit. The trusted layer
    bounds the per-call value to a sane indoor range; allowed zones and a
    user-facing comfort range are agent-stated guarantees.
    """
    pass


@deal.pre(lambda door: len(door) > 0, message="door must be non-empty")
@deal.has("trusted")
@effect(lambda door: DoorLocked.set(door, False))
def unlock_door(door: str) -> None:
    """MOCK trusted door unlock.

    Unlocks ``door``. There is no per-call value the trusted layer can
    sensibly bound here; the meaningful guard is an agent-stated door
    allowlist or an explicit ``never_unlock_door`` contract. Records the
    successor-state effect ``DoorLocked[door] = False``.
    """
    pass


@deal.pre(lambda door: len(door) > 0, message="door must be non-empty")
@deal.has("trusted")
@effect(lambda door: DoorLocked.set(door, True))
def lock_door(door: str) -> None:
    """MOCK trusted door lock.

    Locks ``door``, recording the successor-state effect
    ``DoorLocked[door] = True``.
    """
    pass


@deal.pre(
    lambda item, qty, usd: qty > 0 and usd > 0,
    message="qty and usd must be positive",
)
@deal.has("trusted")
def order_grocery(item: str, qty: int, usd: int) -> None:
    """MOCK trusted grocery order.

    Adds ``qty`` units of ``item`` at ``usd`` total to the next delivery.
    Per-call positivity is enforced by the trusted layer; the household
    budget is an agent-stated guarantee.
    """
    pass
