# Home automation example

The home-automation example is the everyday-stakes counterpart to the
[LIMS example](lims.md). Same relation primitives — allowlists, numeric
aggregation, per-fact bounds — applied to thermostats, door locks, and
grocery orders.

## Trusted module

`set_thermostat` and `order_grocery` carry chained-comparison preconditions
(`30 <= temp_f <= 90`, `qty > 0 and usd > 0`); `unlock_door` has no
sensible per-call value bound, so the meaningful guard is an agent-stated
allowlist or an explicit single-door veto. `unlock_door` and `lock_door` also
declare [successor-state axioms](../reference/fluents.md) over a `DoorLocked`
fluent, used by the door-state-at-end contracts below.

{{ include_file("examples/home-automation/tools/home/trusted/effects.py") }}

The contract vocabulary covers comfort range, door allowlists and vetoes,
and grocery budget and item-count bounds.

{{ include_file("examples/home-automation/tools/home/trusted/contracts.py") }}

## A composed "away mode"

Compose several guarantees to describe a mode. "Away mode" caps the
temperature, leaves only the back door usable for a dog walker, and bounds
any emergency grocery top-up:

{{ include_file("examples/home-automation/cases/away_mode_pass.py") }}

## Allowlist vs single-door veto

`only_unlock_doors(allowed)` and `never_unlock_door(door)` are intentionally
both available. An allowlist is best when the set of acceptable doors is
small; a single-door veto is best when the user wants to forbid one door
without enumerating the rest.

{{ include_file("examples/home-automation/cases/never_unlock_front_door_pass.py") }}

{{ include_file("examples/home-automation/cases/never_unlock_front_door_fail.py") }}

## Door state at the end (fluents)

The contracts above forbid an unlock *event* anywhere in the trace. But a
common errand — unlock the front door, fetch a delivery, lock up again —
*needs* to unlock mid-program; what the user actually cares about is that the
door is locked when the program *ends*.

That is a post-state property, and it is order-sensitive: `unlock; lock` and
`lock; unlock` record the same multiset of facts but leave the door in opposite
states. The relation language cannot tell them apart; a
[fluent](../reference/fluents.md) can. The contracts query the `DoorLocked`
fluent's final valuation:

{{ include_file("examples/home-automation/cases/locked_at_end_pass.py") }}

The door is unlocked partway through but re-locked before the end, so
`all_doors_locked_at_end()` holds. Leaving any door open fails — and unlike a
balanced unlock/lock *count*, the final-state check pins down *which* door is
left open:

{{ include_file("examples/home-automation/cases/locked_at_end_fail.py") }}

Reach for `all_doors_locked_at_end()` / `door_locked_at_end(door)` when the user
cares how the house is *left*, and for `never_unlock_door` / `no_unlocks` when
they want to forbid the unlock from ever happening at all.

## All cases

Browse the full set on the [all-cases page](home-automation-all-cases.md).

## How they run

{{ include_file("examples/home-automation/Justfile") }}
