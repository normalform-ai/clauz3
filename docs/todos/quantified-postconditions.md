# Postconditions that quantify over a returned list

## The gap

A trusted function's postcondition can relate its result to its arguments as
long as the result is a scalar:

```python
@clauz3.ensures(lambda cap, result: result <= cap)
@clauz3.trusted
def count_up_to(cap: int) -> int: ...
```

It cannot say anything about the *elements* of a returned list. The three
claims that matter most in the shipped layers are all of that shape, and all of
them are currently English:

```python
@clauz3.ensures(lambda result: len(result) <= 100)
@clauz3.trusted
def db_query(table: str, where: dict[str, object] | None = None) -> list[UserRow]:
    """... only rows where all key=value constraints hold are returned."""
```

| Effect | The claim | Today |
|---|---|---|
| `db.read_rows` / `db_query` | every returned row satisfies `where` | docstring |
| `find` | every returned path is under the walk root | docstring |
| `grep` | every returned line contains the pattern | docstring |

The consequence is specific and worth stating plainly. `examples/email-from-db`
proves `addresses_from(UserRow, "email")` — every address came from the
query's `email` column — and `count_at_most(100)`. It does **not** prove that
the recipients consented. `db.only_where({"consented": True})` constrains the
*query the program issues*, not the rows that come back; the step from "asked
for consenting users" to "these rows are consenting users" is the trusted
layer's audit, not a proof.

That is a defensible place to draw the line — it is what a trusted base is —
but it is currently invisible: nothing in the guarantee an operator approves
distinguishes a fact the prover established from a docstring it believed.

## Why it does not work

Two separate reasons, either of which is enough:

- `all(... for x in result)` is a `GeneratorExp`, which the expression
  evaluator refuses (`unsupported ast node GeneratorExp`). The
  list-comprehension form fails differently and no better: `cannot resolve
  name all`.
- A returned `list[str]` is not iterable in a proof at all. Only a
  `list[Row]`-returning trusted call can be iterated, via the symbolic
  iteration machinery.

So even a spelling that parsed would only reach the `list[Row]` case.

## Sketch

The row case is the valuable one and the machinery is half there. A for-loop
over `db_query(...)` already introduces a quantified binder and evaluates the
body under it (`FactInfo.quantifiers`, `_active_quantifiers`). A postcondition
over rows wants the same binder introduced at the *call*, and its predicate
added to `ctx.given` rather than `ctx.expected` — the prover assumes a
postcondition, it does not check it.

Roughly:

1. A spelling in the subset. `ensures(lambda where, result: result.all(lambda
   r: r.consented))` reuses the relation-algebra vocabulary the contract layer
   already speaks, and avoids putting comprehensions into the expression
   evaluator.
2. Evaluate it at the call site against the materialized row list, under a
   fresh binder, and conjoin the result into `ctx.given` guarded by the call's
   reachability condition — the same guard `_materialize_trusted_return`
   already applies to a scalar `post`.
3. Decide what a `where`-relating predicate may say. `r.consented == where["consented"]`
   needs dict subscripting of a literal dict argument in the expression
   subset; `where` is already required to be a literal at the call site, so
   this may be a constant fold rather than a new sort.

## Worth doing?

The pull is that it closes the gap between what an approval *says* and what
was proved, for exactly the query-then-act shape this project exists for. The
push-back is that a trusted layer's own body is unproven either way: an
`ensures` over rows is still a claim the trusted layer makes about itself, and
an unsound one makes every proof about its callers unsound. It moves the claim
from a docstring to a checked-at-runtime, read-by-the-prover contract, which is
a real improvement in kind, not in trust.
