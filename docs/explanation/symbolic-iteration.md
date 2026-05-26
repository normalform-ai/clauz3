# Symbolic Iteration

`clauz3` can now reason about for-loops over trusted query returns and prove
column-binding contracts. See
[superpowers/specs/2026-05-25-symbolic-iteration-design.md](../superpowers/specs/2026-05-25-symbolic-iteration-design.md)
for the original design rationale.

## The agent-facing surface

Trusted functions return typed lists of `clauz3.Row` subclasses:

```python
import clauz3
import deal


class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool
    role: str


@deal.has("db_read")
@deal.post(lambda result: len(result) <= 100)
def db_query(table: str, where: dict[str, object]) -> list[UserRow]: ...
```

Agents iterate with normal for-loops:

```python
for row in db_query("users", where={"consented": True}):
    send_email(row.email, "Newsletter")
```

Fixed-size loops over literal lists/tuples or `range(N)` with a literal integer
are unrolled instead of quantified:

```python
for _ in range(5):
    withdraw("checking", 10)

for addr in ["bob@example.com", "ann@example.com"]:
    send_email(addr, "hi")
```

Unrolled iterations emit ordinary concrete facts, so existing aggregates such
as `effect("withdraw").sum(lambda w: w.amount) <= limit` work without the
quantified-aggregate limitations below.

## The contract surface

Contracts can refer to columns:

```python
from clauz3.spec import contract, effect, ContractSpec


@contract
def addresses_from(schema: type, field: str) -> ContractSpec:
    column = getattr(schema, field)
    return effect("send_email").all(lambda e: e.addr == column)
```

Agent usage:

```python
@clauz3.guarantee(emails.addresses_from(UserRow, "email"))
def main() -> None:
    for row in db_query("users", where={"consented": True}):
        send_email(row.email, "hi")
```

The prover verifies that every `send_email` fact's `addr` argument is
*symbolically* the `email` field of a row from a `UserRow` query — not a
literal address, not a different column.

## Worked example

See [`examples/email-from-db/`](../examples/email-from-db.md) for a complete
end-to-end example with multiple cases covering: headline, literal-address fail,
wrong-column fail, mixed-source fail, count bounds.

See [`examples/bank/`](../examples/bank.md) for fixed-bound loop examples that
prove or reject total withdrawal limits over `range(5)`.

## v1 limitations

(see [the spec](../superpowers/specs/2026-05-25-symbolic-iteration-design.md) for full list)

- For-loop bodies cannot use `break`, `continue`, `return`.
- `range(...)` loops are unrolled only for `range(N)` where `N` is a literal
  integer. Symbolic bounds, `enumerate`, `zip`, and dict iteration are not
  supported in v1.
- No state accumulators across loop iterations.
- Row field types are `str`, `int`, `bool` only.
- `where` arguments must be literal dicts at the call site.
- `sum(lambda r: r.numeric_field)` over a quantified fact is unsupported.
  See [docs/todos/quantified-aggregates.md](../todos/quantified-aggregates.md).
- `shares_value` across quantified relations is unsupported.
  See [docs/todos/quantified-shares-value.md](../todos/quantified-shares-value.md).

## How it works (briefly)

The for-loop handler has two modes. For literal lists/tuples and literal
`range(N)`, it unrolls the body once per concrete item and emits ordinary facts.
For trusted query results, it binds the loop variable to `query_result.at(i)`
for a fresh symbolic index `i`, runs the body once symbolically, and snapshots a
`Quantifier` frame into every emitted fact. The relation primitives in
`clauz3.spec` wrap their Z3 body in `ForAll(...)` over fact quantifiers; when no
quantifiers are present, the wrapping short-circuits and the relation behaves as
before.

Column-reference equality (`e.addr == UserRow.email`) is a structural check
on the symbolic expression tree: the prover walks the Z3 expression to verify
it has the shape `<field selector>(array_select(<query result>, ?))` with the
expected schema and field.
