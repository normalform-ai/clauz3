# Returning data from trusted effects

Use this when a query, file parser, or external tool returns data to the agent.
This reference travels with the skill; no Clauz3 checkout is required.

## What the proof knows

A trusted effect's body is skipped during proof. Its return annotation supplies
a symbolic type, and `@clauz3.ensures` narrows its possible values. The body must
actually enforce those promises. A postcondition is an assumption at this trust
boundary, not a proof of an API response, parser, or subprocess implementation.
Runtime postcondition checking can detect a bad return after the body runs; it
cannot undo a request or file write already performed.

Annotate even `-> None`. Scalar `str`, `bool`, `int`, and `float` returns are
supported, as are trusted `list[str]`, concrete `list[Hit]`, and generic
`list[RowT]` with a schema argument. Unsupported types are refused, not treated
as safe. Prefer these documented shapes over arbitrary SDK objects, generators,
or nested record structures. A concrete `clauz3.Row` currently permits only
`str`, `int`, and `bool` fields: a scalar float return being supported does not
make float Row columns supported. For scientific measurements, explicitly
document any chosen string or scaled-integer representation and its units.

## A bounded generic record query

The following is an interface skeleton, **not a working query implementation**.
It raises until an author implements it, so it cannot silently report an empty
successful search. The trusted body may use ordinary Python and an SDK; the
agent's caller remains in Clauz3's supported subset.

```python
# tools/bio/trusted/effects.py
from typing import TypeVar

import clauz3

RowT = TypeVar("RowT", bound=clauz3.Row)


@clauz3.requires(lambda schema, term, limit: 0 <= limit and limit <= 100)
@clauz3.ensures(lambda schema, term, limit, result: len(result) <= limit)
@clauz3.trusted
def query_rows(schema: type[RowT], term: str, limit: int) -> list[RowT]:
    """Return at most limit typed records; implementation required."""
    raise NotImplementedError("Implement the bounded query and schema validation")
```

The result-only alternative is `@clauz3.ensures(lambda result: len(result) <= 100)`.
Use a literal bound there. To relate a result to inputs, declare all function
parameters with matching names, kinds, and defaults, plus `result`, as above.
Do not use a mismatched precondition signature or omit keyword-only defaults.

Implement the adapter by validating the schema and source fields, mapping each
record to an instance such as `schema(accession="P12345", length=42)`, and
enforcing the documented size bound. Reject incompatible/missing field values;
do not claim that a Python type annotation validates an external response.
For a fixed provider record shape, define `Hit(clauz3.Row)` in the trusted
module and return `list[Hit]` instead of accepting an arbitrary schema.

Decide what the cap means: a documented page of results, an explicitly limited
query, or an error if a complete result would exceed the bound. Never silently
truncate a user's request for all records. Test empty results, exact limits,
oversized responses, malformed fields, and provider errors without live requests.

```python
# tools/bio/trusted/contracts.py
from clauz3.spec import ContractSpec, contract, effect
from tools.bio.trusted.effects import query_rows


@contract(description="At most {count} query_rows calls occur in this run.")
def queries_at_most(count: int) -> ContractSpec:
    """Limit calls to the adapter, not its internal HTTP attempts or elapsed time."""
    return effect(query_rows).count() <= count
```

The agent supplies a concrete schema to specialize the generic result:

```python
# query.py
import clauz3
from tools.bio.trusted import contracts as bio
from tools.bio.trusted.effects import query_rows


class Hit(clauz3.Row):
    accession: str
    length: int


@clauz3.guarantee(bio.queries_at_most(1))
def main() -> None:
    hits = query_rows(Hit, "kinase", 5)
    assert len(hits) <= 5
    for hit in hits:
        print(hit.accession, hit.length)
```

The assertion follows from the postcondition, not from examining the query body.
The schema provides field types, not facts such as `length >= 0` or “every
accession is from this database.” A guarantee on later effects must hold for
all allowed row values. Guard individual values before using them as paths,
URLs, or recipients. `all(... for row in result)` is not a supported quantified
postcondition; a docstring promising a filter is not a solver constraint.

Iterate the original trusted result or a direct name alias. Symbolic loops do
not support mutating returned records/containers, reassigning outer variables,
or `break`, `continue`, or `return`. A bound on returned rows can support bounds
on effects performed once per row; a bound on query calls alone cannot.

For a full implementation in a matching Clauz3 source checkout, inspect
`src/clauz3/stdlib/db/tools/db/trusted/effects.py` and its runtime tests.
