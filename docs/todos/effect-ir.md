# Effect IR

## Status

Design todo. This explores whether `clauz3` should have an explicit
intermediate representation (IR) sitting between symbolically executed programs
and the Z3 backend, and whether that IR should be the surface a smart agent
authors directly.

It unifies three threads that are currently described separately:

- the Datalog-frontend idea in [background.md](../explanation/background.md#mechanics-worth-borrowing)
- the [OWL / DL mapping](owl-dl-mapping.md)
- the "agent carries a checkable proof" direction in
  [background.md](../explanation/background.md#proof-carrying-code) and the "formal grammar"
  goal in [python-subset.md](../reference/python-subset.md#design-direction)

These are all describing the same missing middle layer.

## The IR already exists implicitly

The current pipeline is:

```text
agent Python ──(deal-solver symbolic execution)──▶ effect facts ──(spec.py)──▶ Z3 constraints ──▶ SMT check
```

The effect-fact layer is already an intermediate representation. Each trusted
call becomes a guarded effect atom (`_context/_layer.py`):

```python
class FactInfo(typing.NamedTuple):
    name: str
    markers: tuple[str, ...]
    args: dict[str, ProxySort]
    cond: BoolSort          # reachability / path condition
    quantifiers: tuple = ()  # bounded loop variables
```

The contract algebra in `spec.py` (`all`, `empty`, `distinct`, `count`, `sum`,
`shares_value`, …) operates *only* over this set of guarded atoms. It never
looks at the Python again. So the question is not "should we invent an IR" but
"should this fact/contract layer become an **explicit, named, serializable**
artifact with its own grammar."

## Why make it explicit

1. **Smaller trusted computing base.** Today the fail-closed guarantee depends
   on the Python-subset evaluator covering every reachable AST construct. That
   surface is leaky by construction: `for`/`while`, `with`, `try`, `match`, and
   augmented assignment are unsupported (see
   [python-subset.md](../reference/python-subset.md)), and each gap is a place where
   "unprovable" must be enforced rather than guaranteed. A closed IR grammar
   makes fail-closed *structural*: anything not in the grammar cannot be
   expressed, rather than something the evaluator must remember to reject.

2. **It unlocks the static analyses already wanted.** Subsumption,
   contradiction, and redundancy checks over contracts; deterministic
   natural-language summaries for the approval dialog; and the DL/OWL rendering
   all want a declarative object to analyze. That object is this IR, not the
   Python and not the raw Z3 expressions.

3. **It is the right unit for a carried proof.** Re-checking is cheaper and
   lower-TCB than re-deriving. An explicit IR + an SMT proof certificate lets a
   thin checker validate what a heavyweight symbolic executor produced.

## What level: FOL, Datalog, or the Z3 lisp syntax?

These are three different heights, and they serve different roles.

| Layer | Role | Good for | Bad for |
| --- | --- | --- | --- |
| FOL / Datalog-flavored IR | authoring + audit | human review, agent authoring, static analysis | numeric aggregates, string theory (needs more than Datalog) |
| SMT-LIB (Z3 lisp syntax) | object code / proof certificate | cheap re-checking, small checker | authoring, human consent (bakes in sorts + path conditions) |

The authoring/audit IR should be **FOL/Datalog-*flavored***, not pure FOL:

- The semantics is **closed-world over a finite trace** — symbolic execution is
  trusted to have enumerated *all* reachable effects, so absence of an atom
  means the effect does not happen. This is the Datalog / finite-model-checking
  reading, not open-world FOL. The same closed-vs-open mismatch is noted in
  [owl-dl-mapping.md](owl-dl-mapping.md#closed-world-vs-open-world).
- It cannot be *pure* Datalog either, because the system already supports
  `sum`, numeric comparison, and string predicates. Those are SMT theory atoms.

So the honest shape is **"FOL skeleton + theory atoms" = SMT modulo a finite
relation of guarded effect atoms.** That is precisely why the engine is Z3 and
not a Datalog solver.

The Z3 lisp syntax (SMT-LIB) is the **object code**, not the source. It is the
right artifact to *emit* as a proof certificate, but a poor surface to author
and a worse surface to consent to: it bakes in path conditions and Z3 sorts and
is not human-auditable. It should stay underneath as the checker backend.

## Sketch of the IR

A serialized program/contract pair. Illustrative only:

```text
; effect atoms with guards (the trace)
effect send_email(addr, msg) when (path = b0)
effect withdraw(amount)      when (path = b0 and amount <= balance)

; guarantees as FO formulas over the finite effect relation
guarantee forall e in send_email : e.addr in {"bob@example.com"}
guarantee (sum w in withdraw : w.amount) <= 5
```

This is a direct, lossless serialization of the `FactInfo` set plus the
`ContractSpec` tree in `spec.py`. The first milestone is *round-tripping*: lower
Python to this IR, lower this IR to the existing Z3 constraints, and confirm the
proof result is identical to today's.

## Can an agent author the IR directly?

This is the sharpest part of the idea, and it has a load-bearing catch.

The whole safety argument rests on two things: the program is **executable as
one transaction**, and the proof covers **everything that will actually run**.
Symbolic execution earns the second property — the facts provably enumerate all
reachable effects of the *real* code.

If an agent hand-writes a *descriptive* effect IR, that link breaks: the IR
becomes an unverified claim about what will run, reintroducing exactly the trust
the design removes. Therefore:

> Agent-authored IR is only safe if the IR is **executable** — lowered to the
> same trusted calls — not merely descriptive.

At that point the IR is a small guarded-command / effect language with bounded
loops, and the risk is reinventing Starlark. The
recommended posture is to treat a direct-authored IR as a **peer front-end** to
Python: both lower into the same effect IR, and the prover, checker, and
executor are identical regardless of which front-end produced it.

## Recommended shape

```text
          Python  ─┐
                   ├─▶  Effect IR (FOL/Datalog-flavored, executable)  ─┬─▶ Z3 check
   direct-authored ┘    • audit + NL summary + DL view                 └─▶ SMT-LIB proof certificate
       IR (later)       • subsumption / contradiction analysis
                        • lowers to the same trusted calls
```

- Promote the fact + contract layer to an explicit, serializable IR. Python
  symbolic execution lowers into it (today's path, unchanged behavior).
- Build the static analyses and the DL/NL renderings on the IR, not on Python
  or raw Z3.
- Keep SMT-LIB as the backend and optional carried-proof certificate, never as
  the authoring or consent surface.
- Defer direct agent authoring until the IR is executable; treat it as a second
  front-end, not a replacement for the program.

## Work items

1. Specify the IR grammar (effect atoms, guards, quantifiers, the contract
   algebra) as a named, versioned format.
2. Lower the `FactInfo` set + `ContractSpec` tree to the IR (serialize).
3. Re-derive the existing Z3 constraints from the IR (deserialize) and assert
   proof-result parity with the current pipeline across all examples.
4. Build one analysis on the IR end to end (subsumption or contradiction) to
   validate the layer earns its keep.
5. Prototype an SMT-LIB / unsat-proof certificate emitted alongside the IR, and
   a thin checker that validates it without re-running symbolic execution.
6. Only then: prototype a direct-authored, *executable* IR front-end and confirm
   it lowers to the identical trusted calls.

## Relationship to other todos

- [owl-dl-mapping.md](owl-dl-mapping.md) — the DL/OWL view is a *rendering of
  this IR*, not a separate pipeline. The closed-world and aggregate caveats
  there are the same ones that keep the IR at "FOL + theory atoms" rather than
  description logic.
- [quantified-aggregates.md](quantified-aggregates.md) and
  [quantified-shares-value.md](quantified-shares-value.md) — these are gaps in
  the contract algebra that the IR grammar must accommodate or explicitly
  exclude.
- [user-approval-dialog.md](user-approval-dialog.md) — deterministic
  natural-language summaries for the dialog are a consumer of the IR.
