# Agent Guide

You are a home-automation agent with access to thermostat, door-lock, and
grocery-ordering tools. All tool calls go through `clauz3`.

This is the everyday-stakes counterpart to `examples/lims/`: same relation
primitives (allowlist, numeric aggregation, per-fact bounds), different
effect vocabulary. A production trusted layer for home automation would live
in a separate domain repo and be installed with `clauz3 install`.

## Tools

```python
from tools.home.trusted.effects import (
    set_thermostat, unlock_door, lock_door, order_grocery,
)
```

- `set_thermostat(zone, temp_f) -> None` — set a thermostat. Trusted
  precondition: `30 <= temp_f <= 90`.
- `unlock_door(door) -> None` — unlock a door. No per-call value bound; the
  meaningful guard is an allowlist or explicit veto.
- `lock_door(door) -> None` — lock a door. Pairs with `unlock_door` to satisfy
  the door-state-at-end contracts below.
- `order_grocery(item, qty, usd) -> None` — add to next grocery delivery.
  Trusted precondition: `qty > 0 and usd > 0`.

## Available contracts

```python
from tools.home.trusted import contracts as home

home.temp_between(min_f, max_f)         # every thermostat set within range
home.only_zones(allowed)                # thermostat zone allowlist
home.no_unlocks()                       # no door unlocked
home.only_unlock_doors(allowed)         # door allowlist
home.never_unlock_door(door)            # veto one specific door
home.all_doors_locked_at_end()          # every door locked when the program ends
home.door_locked_at_end(door)           # one door locked when the program ends
home.grocery_budget(max_usd)            # total grocery spend bounded
home.grocery_items_at_most(count)       # at most N grocery line items
home.no_groceries()                     # no grocery order placed
home.no_guarantees()                    # explicit null contract
```

## Pattern

Compose multiple guarantees to describe a mode. "Away mode" might be: cool
the house, leave only the back door usable for a dog walker, and cap any
emergency grocery top-up:

```python
@clauz3.guarantee(home.temp_between(55, 65))
@clauz3.guarantee(home.never_unlock_door("front"))
@clauz3.guarantee(home.only_unlock_doors(["back"]))
@clauz3.guarantee(home.grocery_budget(30))
@clauz3.guarantee(home.grocery_items_at_most(2))
def main() -> None:
    set_thermostat("living_room", 58)
    set_thermostat("bedroom", 60)
    unlock_door("back")
    order_grocery("milk", 1, 8)
    order_grocery("bread", 1, 5)
```

`only_unlock_doors(allowed)` and `never_unlock_door(door)` are intentionally
both available: an allowlist is best when the set of acceptable doors is
small; a single-door veto is best when the user wants to forbid one door
without enumerating the rest.

`all_doors_locked_at_end()` / `door_locked_at_end(door)` are *state* contracts,
not *event* contracts. They allow unlocking mid-errand as long as the door is
re-locked before the program ends:

```python
@clauz3.guarantee(home.all_doors_locked_at_end())
def main() -> None:
    unlock_door("front")
    order_grocery("milk", 1, 8)
    lock_door("front")
```

Reach for these when the user cares about how the house is *left*, and for
`never_unlock_door` / `no_unlocks` when they want to forbid the unlock from
ever happening at all.

## Notes

- Use the *strongest true* guarantee that matches the user's intent. "Don't
  unlock the front door" should be `never_unlock_door("front")`, not
  `no_unlocks()`.
- Numeric guarantees compose over straight-line calls and bounded
  `for _ in range(N):` loops.
- Per-call preconditions are enforced by the trusted layer; do not restate
  them as guarantees.
