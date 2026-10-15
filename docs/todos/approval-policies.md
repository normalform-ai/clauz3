# Approval policies

How a **policy admin** lets the approval service decide a request without
waiting for a human, and why that is safe — including for a block list.

## Status

Implemented. `clauz3 approval-service --policy <file.json>` loads a policy and
requires explicit trusted/import root configuration. Requests must select that
operator-authorized layer before evaluation can import Python. A matching rule
resolves the request immediately as `auto_approved` (with a receipt) or
`auto_rejected`; with no match the request stays pending exactly as before.
Evaluation lives in `clauz3.approvals.policy`; the service wires it into
`POST /requests`.

This is the "Policy Interaction" step sketched in
[user-approval-dialog.md](user-approval-dialog.md): hard-deny, auto-pass, else
ask.

## Three roles, three trust levels

| Role | Authors | Concern |
| --- | --- | --- |
| Trusted Layer Engineer (TLE) | trusted-root effects, contracts, and `policy.py` coverage policies | *what the agent can do and should state* |
| Policy admin | the approval policy below | *what the user will accept without being asked* |
| User | per-request decisions in the UI | *this specific request* |

The policy admin is usually the user wearing a "config" hat rather than a "run"
hat: they pre-decide the boring cases so the live dialog is reserved for the
ones that genuinely need a human. The policy is held by the **approval
service** — not the trusted root and not the agent-invoked CLI — which keeps the
decision outside the agent-controlled execution path, the core invariant of the
approval design.

The operator also controls the service's authorized layers. Each layer is a
complete ordered pair of trusted roots and import roots; a request can select
one of those pairs but cannot authorize its own code. Authorization happens
before imports or reuse of a recorded approval, including rules with no
clauses. Unknown selections receive HTTP 403. The operator must protect the
selected files and their dependencies from requester modification. See
[Authorize a layer](../how-to/approval-service.md#authorize-a-layer-for-automatic-decisions)
for CLI and Python configuration.

This is distinct from the TLE's coverage policy
([coverage-policies.md](../reference/coverage-policies.md)). Coverage says what
an agent *should state* about a domain; an approval policy says what the
*service will auto-accept or auto-reject*.

## Decisions are made by entailment

The key move: a rule's clauses are **contract expressions**, and the service
decides whether the program *entails* them — not whether the program's text
*mentions* them. To test a clause, the service conjoins it onto the target as an
extra `@clauz3.guarantee(...)` and re-proves, exactly as the `required` coverage
tier does ([coverage-policies.md](../reference/coverage-policies.md), "How
`required` is enforced"). A clause holds whenever it is logically implied by
what the program proves.

Entailment is what makes both directions sound:

- A program that proves `none()` (sends no mail) entails `at_most(10)` for free;
  a program that sends one literal to an allow-listed address entails
  `only([...])`, `unique_recipients()`, and `at_most(10)` without stating any of
  them. Text matching would miss all of these.
- A block-list clause is an *avoidance obligation*. "Reject unless you have
  proven you avoid the CEO" rejects any program that cannot discharge it —
  including one that reaches a data-dependent recipient under
  `no_guarantees()`. The earlier text-matching prototype could only catch a
  program that openly proved it emails a blocked literal; entailment closes that
  hole.

## Policy file

A JSON file with an `imports` map (aliases the clause expressions resolve
through, pointing at trusted contract modules) and an ordered list of rules:

```json
{
  "version": 1,
  "imports": {
    "pol": "tools.email.trusted.contracts"
  },
  "rules": [
    {
      "name": "deny-blocked-recipients",
      "decision": "auto_rejected",
      "reason": "the program cannot prove it never emails a blocked address",
      "unless_proven": [
        "pol.recipient_at_most('ceo@example.com', 0)",
        "pol.recipient_at_most('press@example.com', 0)"
      ]
    },
    {
      "name": "auto-pass-internal",
      "decision": "auto_approved",
      "reason": "bounded, unique, internal-only email",
      "when_proven": [
        "pol.only(['bob@example.com', 'ann@example.com'])",
        "pol.unique_recipients()",
        "pol.at_most(10)"
      ]
    }
  ]
}
```

The clause vocabulary (`only`, `unique_recipients`, `at_most`,
`recipient_at_most`) is just the trusted layer's contracts; the policy engine
has no email-, bank-, or db-specific knowledge, matching the rest of clauz3.
Clause expressions are syntax-checked at load time; their references are
resolved when a request is proved.

## Decision semantics

The two decisions are duals over one primitive — "does the program entail every
clause?":

| Decision | Clause key | Fires when |
| --- | --- | --- |
| `auto_approved` | `when_proven` | the program entails **all** clauses |
| `auto_rejected` | `unless_proven` | the program does **not** entail every clause |

This is why an allow-list alone is unsafe and the policy admin must make the
auto-pass conjunction complete. `only([...])` permits a single whitelisted
address emailed ten thousand times; adding `unique_recipients()` and
`at_most(10)` to the same `when_proven` list is what bounds it. If the program
did not prove the count and uniqueness contracts, the rule does not fire and the
request falls through to a human. The prover, not the policy engine, closes the
hole.

## Precedence and fallbacks

Deny wins. Every `auto_rejected` rule is evaluated before any `auto_approved`
rule, so an allow-list can never override a block-list. The first matching rule
of the winning kind supplies the decision and its `reason`.

The fallback is always **ask the human** — never a silent pass. A request whose
clauses can't be evaluated (an unresolvable expression, a prover error) skips
that rule rather than guessing, and a program matching no rule is sent to the
UI. Auto-decisions are recorded on the request (`auto_decision`: rule + reason)
and shown on the detail page, so a pass is still auditable.

## Trying it

The fastest loop is `clauz3 policy-check`, which reports the decision a policy
would make for a program without starting a service or executing the submitted
program. This policy-admin tool authorizes its locally selected roots and
imports trusted modules to perform the checks. The `examples/email` Justfile
exercises all three outcomes against `approval-policy.json`:

```bash
cd examples/email
clauz3 policy-check --policy approval-policy.json \
  --trusted-root tools/email/trusted --import-root . \
  --expect auto_approved cases/only_bob_pass.py
clauz3 policy-check --policy approval-policy.json \
  --trusted-root tools/email/trusted --import-root . \
  --expect auto_rejected cases/policy_reject.py   # reaches ceo@example.com
clauz3 policy-check --policy approval-policy.json \
  --trusted-root tools/email/trusted --import-root . \
  --expect ask cases/policy_ask.py                # not blocked, not allow-listed
```

`just policy` runs these. In the live flow, start the service with the same roots:

```bash
clauz3 approval-service --policy approval-policy.json \
  --trusted-root tools/email/trusted --import-root .
```

Point `clauz3 run --trusted-root tools/email/trusted --import-root .` at that
service. An auto-approved program gets an `auto-…` receipt and never appears as a
pending request, an auto-rejected one is denied, and anything in between is sent
to the user.

## Limitations and future work

The [persistent approval profiles proposal](persistent-approval-profiles.md)
describes remembered UI decisions, user-wide storage shared by ephemeral
sessions, context binding, historical curation, and checks on profile edits.
Those features are proposed, not part of the implementation described above.

- **The service re-proves.** Each rule is one prover call against the program
  using an operator-authorized trusted/import root pair selected by the request.
  Those roots must be reachable from where the service runs (true for the
  localhost flow). The service CLI configures one pair; Python hosts can register
  several `ApprovedLayer` instances. Re-proving is
  serialized with a shared layer-selection lock because the prover uses global
  import state. All rules in a request select one verified layer; unchanged
  modules retain identity across requests, including projects with colliding
  names. Clause-set caching remains future work. Approval HTTP waits release
  the selection lock, and runtime resumes the verified image afterward.
- **Entailment is per-program, single-request.** A clause like `at_most(10)`
  bounds one program; the service keeps no ledger across requests, so it cannot
  enforce "at most 10 emails to bob *per day* across many runs." Cross-request
  or cumulative limits need stateful aggregation that does not exist yet.
- **Scalar matching is only as rich as the contracts.** A block-list entry is
  expressed with `recipient_at_most(addr, 0)`; there is no first-class
  "avoid this set" contract, so each blocked address is one clause. Richer
  avoidance and set predicates are trusted-layer work, not policy-engine work.
- **Policy provenance.** Only an explicit `--policy <file>` is supported,
  loaded once at startup. A default location (e.g.
  `.clauz3/approval-policy.json`), hot reload, and signing — the policy is what
  auto-issues receipts without a human, so its integrity matters — are future
  work.
- **Inherits the receipt-enforcement gap.** An auto-approval issues an `auto-…`
  receipt, but trusted functions do not yet verify receipts against bound
  arguments at execution time; auto-approval is exactly as strong as the proof,
  no more.
