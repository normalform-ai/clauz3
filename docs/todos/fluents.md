# Fluents — remaining work

Stateful/fluent contracts (Reiter-style successor-state axioms) landed in v1:
see [the fluents reference](../reference/fluents.md) and GitHub issue #45 for
the design. This todo tracks the gaps left open.

## Effects inside loops

The v1 fold relies on the recorded fact trace being in program order so that
last-write-wins holds. A trusted effect reached under a `for`-loop quantifier
records a *single* guarded fact representing N calls, with no per-iteration
ordering. There is no sound way to fold that into a last-write-wins array, so
fluent contracts over a trace containing a quantified effect fail closed with
an `UnsupportedError`.

Lifting this needs either:

- an ordered, per-iteration representation of loop effects (an explicit fold /
  recurrence over the loop), or
- a restriction to fluent updates that are order-insensitive within the loop
  (e.g. monotone set-insertion fluents) so the quantified fact can be summarized.

This overlaps the quantified-aggregate gaps in
[quantified-aggregates.md](quantified-aggregates.md).

## Computed keys and values

`@effect(lambda door: DoorLocked.set(door, False))` supports a key/value that is
a bound parameter or a literal. Arithmetic, conditionals, and other expressions
over parameters are not supported. Reusing the relation
[lambda subset](../reference/effect-specs.md#lambda-subset) compiler for the key
and value expressions would close this.

## Richer final-state queries

Today `.final` supports `all(predicate)` and per-key `[k] == v`. Existential
(`any`), counting over keys, and comparisons between two fluents' final states
are natural extensions once a use case appears.

## Relationship to the effect IR

The successor-state axiom is exactly the kind of structured effect metadata the
[effect-IR todo](effect-ir.md) wants to carry alongside the `@deal.has` markers.
When the IR becomes explicit, fluent updates should be a first-class node in it
rather than a side registry on the `Fluent` object.
