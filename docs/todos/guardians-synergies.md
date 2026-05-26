# Guardians Synergies

## Status

Design todo. Tracks possible synergies between `clauz3` and
[`metareflection/guardians`](https://github.com/metareflection/guardians), the
open-source implementation of Meijer's *Guardians of the Agents: Formal
verification of AI workflows*
([ACM Queue 23(4), 2025](https://queue.acm.org/detail.cfm?id=3762990);
[doi:10.1145/3777544](https://doi.org/10.1145/3777544)).

See [background § Guardians](../explanation/background.md#guardians) for the
related-work framing. This document is the actionable companion: what the two
systems could borrow from each other, where each track would attach in the
current code, and the open questions.

## The shape of the overlap

Guardians and `clauz3` both **verify before any side effect runs** and both
treat a checkable artifact — not agent prose — as what authorizes execution.
The two designs differ on three axes that the tracks below try to bridge:

| Axis | Guardians | `clauz3` |
| --- | --- | --- |
| Authored surface | structured workflow AST with `SymRef` placeholders | ordinary Python, symbolically executed |
| Policy authorship | operator-defined `Policy` | agent-abduced contract, user-consented |
| What it can express | taint source→sink, security automata over ordered tool calls, Z3 pre/post/frame | relational contracts over an *unordered* set of guarded effect facts |

The effect-fact layer is the join point. Each trusted call lowers to a
`FactInfo` (`name`, `markers`, `args`, `cond`, `quantifiers` — see
[effect-ir](effect-ir.md#the-ir-already-exists-implicitly)), and `spec.py`'s
relation algebra (`all`, `empty`, `distinct`, `count`, `sum`, `shares_value`)
reasons only over that set. Every track here is, at bottom, a question about
what to add to those facts and to that algebra.

## Track 1: Taint and provenance in the fact layer

**Goal.** Express source-to-sink rules such as "a value returned from
`fetch_mail` must not reach `send_email.body` unless sanitized" — the
prompt-injection class Guardians targets with `TaintRule`.

**Where it attaches.** `FactInfo.args` currently holds the symbolic value of
each argument but no record of *where that value came from*. The
[email-from-db](../examples/email-from-db.md) example already carries a partial
provenance anchor: a value bound from a trusted query's returned column is
matched structurally against `UserRow.email` (see `_compare_column_ref` in
`spec.py`). Generalizing that — every value flowing out of a trusted *read*
carries a source label, and the label propagates through assignments — is the
provenance lattice the [background](../explanation/background.md#mechanics-worth-borrowing)
"provenance facts" note asks for.

**Open design choice (from the issue).** Whether a source→sink rule is:

1. a new relation primitive in `clauz3.spec` (e.g. `Email.all(lambda e: not
   tainted(e.body, source="fetch_mail"))`),
2. a separate policy layer that runs over the fact set after the relational
   contracts, or
3. a trusted-domain helper authored by the TLE alongside `only`/`unique_recipients`.

The [effect-IR](effect-ir.md) direction argues for making provenance an
explicit part of the fact grammar so all three can be expressed as analyses over
one artifact, rather than bolted onto the Python evaluator.

## Track 2: Security automata over effect traces

**Goal.** Policies that are about *order*: "an approval/check effect must precede
any `transfer`", "after a privilege escalation, only these effects may occur".
Guardians models these as a `SecurityAutomaton` (states, transitions keyed by
tool name, error states).

**Where it attaches.** Today the facts are an unordered set; each carries a path
condition (`cond`) but no position in a trace. Per-effect relational constraints
cannot express "X before Y" cleanly. This track needs either (a) an ordering
relation over facts, or (b) an explicit automaton checker that walks the facts
in execution order and rejects on reaching an error state. Bounded loops
(`FactInfo.quantifiers`) complicate ordering and need a story — likely the
automaton reasons over loop bodies as a unit first.

This is the track with the least existing scaffolding in `clauz3` and the
clearest "Guardians is ahead" gap.

## Track 3: Workflow front-end / effect-IR bridge

**Goal.** Let a Guardians-style workflow and a `clauz3` Python program be two
front-ends onto the same prover, rather than competing systems.

**Where it attaches.** [effect-ir](effect-ir.md#can-an-agent-author-the-ir-directly)
already lands on the right posture for this: a non-Python front-end is safe
**only if it is executable** — lowered to the same trusted calls — not merely a
descriptive plan. A Guardians workflow AST whose nodes bind to the same trusted
effects could lower into the effect IR and reuse the identical prover, checker,
and executor. Conversely, `clauz3` trusted roots could *emit* Guardians
`ToolSpec`s and a starter `Policy`, since the `@deal.pre`/`@deal.has`/signature
data already encodes most of a `ToolSpec`.

The payoff is to get both properties: Guardians' workflow-before-data
prompt-injection resistance *and* `clauz3`'s Python-level proof and
consent artifact.

## Track 4: Approval UX / literate workflows

**Goal.** Combine Guardians' explainable structured workflow with `clauz3`'s
proved-contract-as-consent surface.

**Where it attaches.** The [approval-dialog sketch](user-approval-dialog.md)
already wants a deterministic natural-language summary of the proved contract;
[effect-ir](effect-ir.md) names that summary as a consumer of the IR. Guardians'
literate workflow is the supporting detail: show the proved contract first (the
thing the user consents to), with a structured trace/workflow explanation
demoted alongside it, like a cover letter on a signed document (see
[background § DocuSign analogy](../explanation/background.md#a-loose-docusign-analogy)).

## Track 5: Shared examples / benchmark

**Goal.** Make the overlap and the real differences concrete, and give a basis
for collaboration.

**Where it attaches.** The [background](../explanation/background.md#mechanics-worth-borrowing)
"benchmarks" note already flags the absence of an adversarial corpus. Recreate
Meijer's exact scenario in `examples/`: a malicious inbox message from
`it@othercorp.com` instructs the agent to silently forward a summary of
Michelle's mailbox to that external address, where the only sanctioned internal
domain is `valleysharks.com`. Then implement it two ways:

1. **Today's `clauz3`.** A program that forwards to `it@othercorp.com` cannot be
   proved under `emails.only(["michelle@valleysharks.com", ...])` — the existing
   allowlist contract already rejects the *destination* of the exfiltration,
   which mirrors Meijer's automaton condition `to notIn ["*.valleysharks.com"]`.
   This shows how far the current relational model gets with no new machinery.
2. **A taint/provenance extension (Track 1).** Catch the source→sink version
   directly, as Meijer's taint policy does: the email *body* carrying data read
   from `fetch_mail` is rejected even when the destination is allowed. This is
   the case the current model cannot express.

A useful third panel is the failure mode each design avoids: a pure runtime
monitor that blocks the forward only *after* the inbox has been read and a first
effect committed.

## Open questions

- Is workflow-before-data compatible with the coding-agent style `clauz3`
  targets, or should it be an optional front-end? (The
  [effect-IR](effect-ir.md#recommended-shape) recommendation is "peer
  front-end, not replacement.")
- Can `clauz3`'s relation model express source/sink taint naturally, or does it
  require a separate provenance lattice? (Track 1.)
- Should operator-defined policies stay distinct from agent-proposed guarantees,
  or should the `required`/`recommended` coverage policies in `policy.py` grow
  into a fuller Guardians-like policy layer? (See
  [background § Operator Policy](../explanation/background.md#operator-policy-versus-agent-abduced-contracts)
  and [coverage policies](../reference/coverage-policies.md).)
- What is the minimum bridge demo worth building first? Track 5's email
  exfiltration case is the current candidate.

## Proposed next step

Build the email-exfiltration demo (Track 5) against the existing email trusted
layer, showing the destination-level rejection with today's contracts. Use it to
scope the smallest taint/provenance extension (Track 1) that adds the
source→sink rejection, and to decide which of the three attachment points above
that extension should take.
