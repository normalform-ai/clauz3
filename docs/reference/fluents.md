# Fluents (stateful contracts)

The [effect-spec relation language](effect-specs.md) treats trusted calls as an
unordered multiset of guarded facts. Contracts are universal, existential, or
aggregate queries over that bag. That is enough for "who did we email", "how
much did we spend", and "what paths did we write", but it cannot talk about
*post-state* or *order*.

A motivating example from home automation:

> An agent unlocks the front door, enters, retrieves an object, leaves, locks
> the front door. **Contract: the door is never left unlocked at the end.**

You cannot express this as a relation query. `Unlock.count() == Lock.count()`
is only a necessary condition, and it is not even sufficient: `lock; unlock`
and `unlock; lock` record the *same* fact set but leave the door in opposite
states. The post-state depends on the *order* of effects, which the relation
language deliberately discards.

Fluents add that missing dimension, modeled on Reiter's situation-calculus
successor-state axioms.

## Declaring a fluent

A fluent is a named, keyed, mutable cell. The trusted layer declares it
alongside its effects:

```python
from clauz3.fluent import effect, fluent

DoorLocked = fluent("door_locked", key=str, value=bool, initial=True)
```

- `key` — the type of the cell's index (`str` for door/zone/device names,
  `int` for numeric ids).
- `value` — the type stored in the cell (`bool`, `int`, or `str`).
- `initial` — the value every key holds before any trusted call runs.

## Declaring successor-state axioms

A trusted function declares how it mutates fluents with the `@effect`
decorator. The builder's parameters mirror the trusted function's parameters,
and it returns one or more `SomeFluent.set(key, value)` assignments:

```python
import deal


@deal.pre(lambda door: len(door) > 0)
@deal.has("trusted")
@effect(lambda door: DoorLocked.set(door, False))
def unlock_door(door: str) -> None: ...


@deal.pre(lambda door: len(door) > 0)
@deal.has("trusted")
@effect(lambda door: DoorLocked.set(door, True))
def lock_door(door: str) -> None: ...
```

`@effect` is inert at runtime, like `clauz3.guarantee`. It only records, on each
referenced fluent, how the call mutates it so the prover can fold it into the
final state. Because the axiom lives in the *trusted* layer, the trusted layer
*enforces* it — unlike an honor-system `end_session(front_locked)` precondition,
the agent cannot lie about having re-locked the door.

`@effect` is a trusted-layer construct and is rejected in agent-authored entry
files, exactly like `@deal.has` and `@contract`.

## Contracts over the final state

Contracts query the fluent's valuation *after the whole program has run* via
`.final`:

```python
from clauz3.spec import ContractSpec, contract


@contract
def all_doors_locked_at_end() -> ContractSpec:
    return DoorLocked.final.all(lambda d: d.value == True)


@contract
def door_locked_at_end(door: str) -> ContractSpec:
    return DoorLocked.final[door] == True
```

- `fluent.final.all(predicate)` — every key's final value satisfies the
  predicate. The predicate receives a row exposing `d.value` (and `d.key`),
  using the same [lambda subset](effect-specs.md#lambda-subset) as relations.
- `fluent.final[key]` — the final value at one key, comparable against a literal
  with `==` / `!=`.

These are *state* contracts, not *event* contracts. They allow a door to be
unlocked mid-program as long as it is re-locked before the end — which is
exactly what the relation-level `never_unlock_door` / `no_unlocks` cannot
express, and vice versa.

## How it is encoded

Each fluent is a Z3 array over its key sort. The prover folds it through the
recorded fact trace in program order:

```text
arr₀ = K(KeySort, initial)                          # constant array
arrᵢ = If(callᵢ.reachable, Store(arrᵢ₋₁, keyᵢ, valᵢ), arrᵢ₋₁)
```

Each contributing call applies a *guarded* store, so only reachable calls mutate
the array and the last write along any path wins. `.final[k]` is `Select(arr, k)`
and `.final.all(p)` is `∀k. p(Select(arr, k))`.

This is the same array-threading the program subset already uses for local
variables, lifted onto the trusted layer's effect declarations. It runs entirely
at contract-solve time over `ctx.facts`, which symbolic execution has already
flattened into program order (branch facts are merged into one guarded layer),
so the executor itself is unchanged.

## Limitations (v1)

- **No effects inside loops.** A trusted effect reached under a `for`-loop
  quantifier records a single guarded fact with no per-iteration ordering, which
  is incompatible with last-write-wins. Such cases fail closed with an
  `UnsupportedError`. See [the fluents todo](../todos/fluents.md).
- **Key/value types** are limited to `str`, `int`, and `bool`.
- **Successor-state axioms** assign a parameter or a literal to a key that is a
  parameter or a literal. Computed keys/values (arithmetic, conditionals) are
  not supported yet.
- Like all `@contract` helpers, fluent contract bodies are still executed as
  Python; see [What is still trusted](effect-specs.md#what-is-still-trusted).
