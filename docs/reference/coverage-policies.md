# Coverage policies

How a trusted layer states which guarantees an agent *should* or *must* make
about a domain it uses, and how the result reaches the user. This is the
"meta-reasoning" layer: it reasons about the **set** of guarantees attached to
a program, not about the program's traces.

## The problem: silent omission

A program can import several trusted domains — email, a database, a bank — and
attach a strong guarantee to one while saying nothing about another. An absent
guarantee and an explicit [`no_guarantees()`](effect-specs.md#no_guarantees)
look the same to a user skimming the approval dialog: in both cases the domain
is unconstrained. `no_guarantees()` exists to make weakness *visible*, but
nothing forces the agent to write it.

Coverage policies close that gap. They let the Trusted Layer Engineer (TLE)
declare, per domain, which contracts matter — and the prover and approval
service then surface (or enforce) the difference between *stated* and *used*.

## The manifest

A trusted root may contain a `policy.py` exposing a module-level `POLICY` — a
`DomainPolicy` (or a sequence of them):

```python
# tools/email/trusted/policy.py
from clauz3.policy import domain_policy

POLICY = domain_policy(
    when_used="send_email",                  # trusted fn that activates the domain
    recommended=["only", "unique_recipients"],
    required=[],                             # nullary invariants (see below)
    label="email",
)
```

A policy names contracts by their bare `@contract` name (`only`,
`unique_recipients`). It is plain data: loading it runs no agent code and adds
no solver work for the recommended/silent checks. The loader finds it via the
same trusted-root discovery used for effects and contracts, so no extra
configuration is needed — dropping a `policy.py` into a trusted root is enough.

Because policies live in the trusted root, only the TLE can write them. Agent
code is forbidden from declaring its own obligations, exactly as it is forbidden
from writing `@deal.has`, `@contract`, or `@clauz3.solver`.

## `recommended` vs `required`

These are two different mechanisms, not two severities of one:

| Tier | Meaning | Effect |
| --- | --- | --- |
| `recommended` | the agent *should* state this | a coverage flag in the approval UI; never blocks the proof |
| `required` | the agent's program *must* satisfy this | a proof obligation conjoined into the proof; a violation rejects the program before approval |

`recommended` is the genuine meta-reasoning: a lint over the guarantee set.
`required` is closer to assume/guarantee reasoning — the TLE contributes a
guarantee that holds whenever the effect is reachable, whether or not the agent
mentioned it.

## How "used" is determined

A domain counts as **used** when the program contains a call to its `when_used`
function. This is a static, AST-level over-approximation: a `send_email` call in
a dead branch still counts. For a *warning*, over-approximation is the safe
direction — it errs toward telling the user more, not less.

## Coverage statuses

For each used domain, `compute_coverage()` reports one status, derived from the
agent's guarantee expressions (matched to a domain by resolving the guarantee's
import alias back to the trusted package):

| Status | Condition |
| --- | --- |
| `covered` | all recommended/required contracts are stated |
| `recommended_gap` | the domain is addressed, but a recommended contract is missing |
| `silent_gap` | the domain is used, but the agent stated **no** guarantee about it |
| `required_gap` | a required contract is missing |

Alias resolution is what stops a false `silent_gap` when the agent used a real
but unlisted contract such as `emails.only(...)`.

The report is attached to the approval request under a `coverage` key, alongside
`guarantees` and `proofs`. The approval service renders it as a **Coverage**
section on the request detail page — gaps sorted above covered domains, with
`silent_gap` and `required_gap` styled loudest — and a per-request badge on the
index showing the worst status, so a reviewer sees a flag at a glance.

## How `required` is enforced

When a target function uses a `required` contract's triggering effect, the
prover conjoins that contract into the proof. It does this by rewriting only the
**proved copy** of the source: it adds a `@clauz3.guarantee(<contract>())`
decorator (and the import it needs) so the obligation resolves through the same
inference-and-registry path as an agent-written guarantee. The executed program
and the approval request keep the original, unmodified source.

Conjoining is sound even for domains that are not used: a relational contract
over an empty fact set (`all`, `distinct`, `empty`, bounded `count`/`sum`) is
vacuously true. Gating on use keeps the behaviour predictable and matches the
"obligation triggered by use" intent.

Because the TLE cannot know agent-supplied arguments, **required contracts must
be nullary invariants** — `unique_recipients()`, not `only([...])`. A required
name that is non-nullary or not a registered `@contract` raises a
`ProverConfigError`.

## Limitations and future work

- **Coverage is mention-level, not entailment-level.** A recommendation is
  "covered" when the agent states a contract of that name, not when the proved
  guarantee logically *implies* it. Entailment-strength coverage would need a
  subsumption check in Z3 — the same machinery as the
  [subsumption-over-policies](effect-specs.md#relationship-to-datalog-and-sql)
  future work.
- **`required_gap` labelling.** With enforcement on, a `required_gap` reaching
  the UI means "we enforced it for you and it held" (a violation would have been
  rejected earlier). A future label could distinguish *auto-enforced* from
  *missing*.
- **Key policies on function names, not markers.** A marker like `@deal.has(...)`
  can match facts with non-uniform shapes; keying `when_used` on the trusted
  function name keeps the activation signal robust.
