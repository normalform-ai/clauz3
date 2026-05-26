# Generic Effect Specs

This document describes the current contract-spec prototype. It is the path
away from domain-specific solver callbacks and toward reusable, auditable
contract libraries.

## Goal

Domain authors should be able to write contracts like:

```python
Email = effect("send_email")


@contract
def only(addresses: list[str]) -> ContractSpec:
    return Email.all(lambda e: e.addr in addresses)
```

without knowing about `ctx.facts`, z3 proxy objects, or the vendored
`deal-solver` internals.

The core idea is that every trusted function invocation creates a row in a
finite relation. Contracts are relational constraints over those rows.

## Trusted Functions Become Effect Facts

A trusted function is an audited side-effect boundary:

```python
import deal


@deal.pre(lambda account, amount: amount >= 0)
@deal.has("trusted")
def withdraw(account: str, amount: int) -> None:
    ...
```

When the prover sees a reachable call:

```python
withdraw("checking", 3)
```

it records a symbolic fact like:

```python
name = "withdraw"
markers = ("trusted",)
args = {"account": "checking", "amount": 3}
cond = <path condition>
```

The trusted function body is not symbolically executed. Its preconditions are
proved as assertions, then the call is recorded as a fact.

This is generic. The core does not know what a withdrawal, email, HTTP request,
or file write means. It only records the trusted function name, markers,
signature fields, symbolic arguments, and reachability condition.

## Effect Relations

`effect(name)` creates a relation view over trusted facts:

```python
Withdraw = effect("withdraw")
Trusted = effect("trusted")
```

The name can match either:

- a trusted function name, such as `withdraw` or `send_email`
- a marker from `@deal.has(...)`, such as `trusted` or `stdout`

For most domain contracts, prefer the trusted function name. It gives a clear
schema because the fields come directly from that function's signature.

Markers are useful for cross-cutting groups, but they are looser. If several
functions share a marker but have different arguments, a marker-level relation
may not have a uniform row shape. The current implementation fails only when a
contract references a field missing from a matched fact; future versions should
validate marker schemas earlier.

## Relation Primitives

The current relation API is intentionally small.

### `no_guarantees`

`no_guarantees()` is the transparent null contract. It solves to `True` and
imposes no constraints:

```python
from clauz3.spec import no_guarantees as core_no_guarantees


@contract
def no_guarantees() -> ContractSpec:
    return core_no_guarantees()
```

This is semantically equivalent to omitting the guarantee, but it is useful in
user-facing flows because it lets an agent explicitly say, "for this domain, I
make no guarantees."

### `all`

Every reachable row must satisfy a predicate:

```python
Email.all(lambda e: e.addr in addresses)
Withdraw.all(lambda w: w.account == account)
```

The path condition is handled by the relation primitive. Authors do not write
`if reachable then ...` manually.

### `empty`

No reachable row may exist:

```python
Email.empty()
```

This is used by `emails.none()`.

### `distinct`

No two reachable rows may have the same key:

```python
Email.distinct(lambda e: e.addr)
```

This proves "never email the same person twice." Mutually exclusive branches are
allowed to produce the same key because both rows cannot be reachable in the
same execution.

### `sum`

Sum a selected numeric field over reachable rows, then compare it:

```python
Withdraw.sum(lambda w: w.amount) <= limit
```

Unreachable rows contribute zero.

### `count`

Count reachable rows, then compare the count:

```python
Email.count() <= 2
```

### `where`

Filter a relation before applying another relation primitive:

```python
Email.where(lambda e: e.addr == "bob@example.com").count() <= 2
```

This proves policies like "Bob is emailed at most twice." The filter is also
guarded by reachability conditions, so facts in unreachable branches do not
contribute to the filtered count.

### `shares_value`

Require two filtered relation views to have at least one reachable pair with
the same selected value:

```python
bob = Email.where(lambda e: e.addr == "bob@example.com")
ann = Email.where(lambda e: e.addr == "ann@example.com")
bob.shares_value(ann, lambda e: e.msg)
```

This proves policies like "Bob and Ann receive the same email content." It is an
existential join: at least one Bob row and at least one Ann row must be
reachable with equal selected values.

## Lambda Subset

Relation lambdas are parsed from source and compiled by `clauz3.spec`.
Supported today:

- constants: `bool`, `int`, `str`
- list literals and captured lists
- row attributes: `e.addr`, `w.amount`
- comparisons: `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`
- boolean operators: `and`, `or`, `not`
- numeric `+` and `-`
- `len(...)` over supported row values such as strings
- `str.startswith(prefix)` and `str.endswith(suffix)` over string row values,
  for path-prefix policies such as `e.path.startswith(root)`

Unsupported constructs fail closed with an `UnsupportedError`. Notably,
arbitrary function calls inside relation lambdas (other than `len(...)` and the
whitelisted string methods above), loops, mutation, and dynamic attribute
access are not supported in the spec layer yet. See
[ClauZ3 Python Subset](python-subset.md) for the full current subset.

## What Is Still Trusted

This prototype reduces the trust burden, but it does not eliminate it.

Trusted today:

- trusted roots such as `tools/email/trusted/`
- the vendored and modified `deal-solver` machinery
- the `clauz3.spec` compiler and relation primitives
- domain helper builder bodies marked with `@contract`

The last item is the main remaining gap. A helper like this is easy to audit:

```python
@contract
def max_spend(limit: int) -> ContractSpec:
    return Withdraw.sum(lambda w: w.amount) <= limit
```

but it is still executed as Python. Future work should validate helper bodies by
AST before executing or compile them without executing arbitrary Python.

## Why Not Hard-Code Email?

The effect relation is derived from trusted functions, not from domain names.
Trusted functions and domain contracts live together under trusted roots.

Email:

```python
@deal.has("trusted")
def send_email(addr: str, msg: str) -> None: ...

Email = effect("send_email")
```

Bank:

```python
@deal.has("trusted")
def withdraw(account: str, amount: int) -> None: ...

Withdraw = effect("withdraw")
```

Both follow the same rule: the trusted function name creates the relation, and
the function parameters create the row fields.

## Relationship To Datalog And SQL

Most useful side-effect contracts are relational:

- allowlists are universal predicates over rows
- "never twice" is a uniqueness constraint
- budgets are aggregates
- read-only is absence of write rows
- table policies are set membership over database action rows

The current Python API is deliberately small, but it is close to a relational
algebra. A later frontend could be more explicitly Datalog-like or SQL-like and
compile to the same internal relation primitives.

Useful future features:

- named relation schemas for marker-level effects
- richer string predicates (substring, regex) beyond the current
  prefix/suffix checks
- `exists`, `any_of`, and grouped aggregates
- ~~provenance facts to describe where an argument value came from~~ —
  the common case (value from a column of a trusted query result) is now
  addressed by symbolic iteration; see
  [symbolic-iteration.md](../explanation/symbolic-iteration.md).
- contradiction, redundancy, and subsumption checks over policies — a first,
  mention-level completeness check now exists; see
  [Coverage policies](coverage-policies.md)
- deterministic natural-language rendering for approval dialogs
- description logic / OWL renderings for contracts; see
  [OWL / Description Logic Mapping](../todos/owl-dl-mapping.md)

## Runtime Role

Static proof answers whether the whole program satisfies its contract before
any trusted side effect happens. That is important for transactional intent: the
system can reject the entire program rather than discovering halfway through
that the next effect is forbidden.

A runtime monitor can still be useful as defense in depth, but it should enforce
the already-proved contract rather than replace the static proof step.
