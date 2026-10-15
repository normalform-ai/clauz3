# Avoid vacuous proofs

`main: proved!` means every guarantee follows from the trusted layer's
assumptions on every branch of the program. It does not mean the guarantee
ranged over anything. This page collects the ways a guarantee can hold while
constraining nothing, says who can close each gap, and shows how. The first is
an obligation on trusted-layer authors; the others are proof and test boundaries whose
shape you should recognise.

## Guarantees over external identifiers

Take the [home-automation example](../examples/home-automation.md) with a real
thermostat bridge behind `set_thermostat`. The bridge knows three zones —
`living room`, `dining room`, `bedroom` — and the agent writes:

```python
@clauz3.guarantee(home.only_zones(["Dining Room"]))
@clauz3.guarantee(home.temp_between(60, 75))
def main() -> None:
    set_thermostat("Dining Room", 68)
```

```console
$ clauz3 prove --trusted-root tools/home/trusted cases/dining_room_pass.py
main: proved! assertion, guarantee, guarantee
```

The proof is correct: every thermostat call targets a zone in the allowlist,
as a string. But the bridge has no `Dining Room` (it has `dining room`), and if
the effect wraps a library that looks the name up and returns `None` on a miss
(for example, a client that logs a lookup failure instead of raising), then
`clauz3 run` approves, executes `main`, exits 0, and the setpoint never
changes. Every guarantee held. Nothing happened.

This is not a prover bug. `clauz3` cannot know a bridge's zone names at prove
time, and soundness is intact: the stated property genuinely held. The gap is
*referential* — proven properties of an identifier say nothing about whether
the identifier denotes anything. The same applies to any opaque identifier a
guarantee ranges over: room and device names, file paths, table names, branch
names, email addresses.

**The obligation: an effect that resolves an identifier against external state
must raise on no-match, never return quietly.** The trusted-layer author is the
only one positioned to enforce it, and the strength of every guarantee over
that identifier is bounded by whether they did.

### Before and after

The body runs only under `clauz3 run`; the prover never evaluates it, so both
versions below prove identically. The difference is visible only at run time.

Before — resolution failure is swallowed:

```python
@clauz3.requires(lambda zone, temp_f: 30 <= temp_f <= 90, message="temp_f in [30, 90]")
@deal.has("io")
@clauz3.trusted
def set_thermostat(zone: str, temp_f: int) -> None:
    zones = bridge.zones()
    if zone not in zones:  # no such zone: return quietly
        return
    bridge.set(zone, temp_f)
```

After — resolution failure raises, naming what the bridge actually has:

```python
class UnknownZoneError(LookupError):
    """``zone`` does not name a zone the bridge knows."""


@clauz3.requires(lambda zone, temp_f: 30 <= temp_f <= 90, message="temp_f in [30, 90]")
@deal.has("io")
@clauz3.trusted
def set_thermostat(zone: str, temp_f: int) -> None:
    zones = bridge.zones()
    if zone not in zones:
        raise UnknownZoneError(f"no zone {zone!r}; bridge has {sorted(zones)}")
    bridge.set(zone, temp_f)
```

Same program, same proof, different run:

```console
$ clauz3 run --trusted-root tools/home/trusted cases/dining_room_pass.py   # before
main: proved! assertion, guarantee, guarantee
approval: approved_once receipt=...
executed: main

$ clauz3 run --trusted-root tools/home/trusted cases/dining_room_pass.py   # after
runtime error: no zone 'Dining Room'; bridge has ['bedroom', 'dining room', 'living room']
```

An invisible no-op becomes a one-line diagnosis, and `clauz3 run` exits 1. No
`@deal.raises` declaration is needed — the prover never evaluates the body —
and adding one makes `clauz3 lint` ask you to enumerate every other exception
the body can raise.

When wrapping a third-party client, check what its lookup does on a miss. Any
`get_*_by_name` that logs and returns `None` or `False` is the live hazard;
check its result and raise. Listing the identifiers that do exist in the
message is cheap and turns a miscased name into an immediate fix.

Some effects have quiet semantics on purpose: an `rm -f`-style delete that
tolerates a missing path, an environment read that returns `""` for an unset
variable. That is a legitimate choice, but say so in the effect's docstring,
because a guarantee over that identifier is then intent-only: it bounds what
the program *may* touch, not what it *did*.

### Test it at run time

Proof cases (`*_pass.py` / `*_fail.py`) cannot catch this; they never run the
body. Give the layer a runtime smoke test that calls each resolving effect with
an identifier that does not exist and asserts it raises. `stdlib:db` ships one
as `tests/runtime_smoke.py`, wired to a `runtime-smoke` recipe in its
`tests/Justfile`.

```python
with pytest.raises(UnknownZoneError):
    set_thermostat("No Such Zone", 68)
```

For each identifier parameter, one of two things should be true: it is
validated at the boundary and raises, or the effect's docstring states its
quiet semantics and guarantees over it are read as intent-only.

## Other ways `proved!` can say less than it seems

These checks address different failure modes; none establishes that an
external identifier exists.

**Contradictory assumptions — caught.** Every obligation is discharged as
`assumptions -> guarantee`, so assumptions that cannot hold make every guarantee
vacuously true. Since [#105](https://github.com/cmungall/agent-deal/pull/105)
the prover checks the quantifier-free assumptions for satisfiability first and
reports a non-successful `vacuous` conclusion when they are contradictory
(`skipped` when undecided). This does not touch the identifier gap above: the
thermostat program's assumptions are satisfiable and its guarantee really
holds. See [Trusted conditions](../reference/python-subset.md).

**Malformed guarantees — caught.**
`@clauz3.guarantee(home.only_zones)` names a helper without calling it and is
refused at prove time. Guarantees must call a registered trusted `@contract`
helper with literal arguments or permitted `Row` class references. Bare names,
entry-file look-alikes, lambdas, and malformed decorator calls are refused
before approval. See [The guarantee grammar](../reference/python-subset.md#the-guarantee-grammar).

**A `_fail` case that passes on a skip — not caught.** The Justfile idiom
`if {{prove}} cases/x_fail.py; then exit 1; fi` accepts any non-zero exit,
including `skipped`. When two trusted layers share a dotted package name
(`tools.db` from an example and from `stdlib:db`), an import-cache collision
can cause a configuration error rather than a counterexample. The loader now
refuses a module cached from a different root; historically, a
path-traversal `_fail` case passed CI after importing the wrong layer
([#111](https://github.com/cmungall/agent-deal/issues/111)). A `_fail` recipe
proves something only when the output reads `failed`, so assert on that rather
than on the exit code alone (the same tightening
[#107](https://github.com/cmungall/agent-deal/issues/107) asks of the pytest
suite), and keep package names unique across every root a project loads.

**A contract over the wrong relation — author responsibility.** A relation
constrains only the trusted functions it selects. An empty selection can satisfy
an `all(...)` or `empty()` guarantee without constraining the intended effect.
Current libraries use explicit effect relations and `@clauz3.trusted` function
identity; `@deal.has(...)` markers provide Deal runtime/lint behavior, not a
cross-library permission boundary. When composing layers, inspect the trusted
contract's relation membership and use each layer's own contracts. See
[Effect specs](../reference/effect-specs.md) and
[Coverage policies](../reference/coverage-policies.md).
