# Background And Related Work

`clauz3` is a static contract layer for agent-authored Python. The agent
writes a short program and proposes a side-effect contract over it; a Z3-backed
prover either discharges the contract or rejects it; the discharged contract is
the artifact the user consents to. Runtime execution is then all-or-nothing.
There is no per-call gate in the current design.

This is different from most agent permission systems, which either grant broad
capabilities up front or intercept individual tool calls at runtime.

## Capability Permissions Are Too Coarse

"This agent may use email" is not the same thing as:

- it may email only Bob
- it may email Bob at most once
- it may not send email at all
- it makes no guarantees about email

`clauz3` treats the contract as the permission request. The agent can ask
for a weak contract, but weakness is visible to the user and to saved policy.
The explicit null contract, `emails.no_guarantees()`, is useful for exactly
that reason: it is semantically equivalent to no constraint, but it is not
silent.

## Static Proof Versus Runtime Blocking

Runtime policy monitors can block individual effects. That is useful, but it
has an awkward failure mode for transactional programs: the first side effect
may already have happened when the second one is denied.

Static proof instead accepts or rejects the whole program before any trusted
effect runs. The cost is that the prover has to reason about all relevant
branches ahead of time, and the contract language has to stay inside a tractable
subset.

The strongest eventual system may do both:

- prove the contract statically before execution
- enforce a matching receipt at runtime as defense in depth

## Proof-Carrying Code

The closest ancestor is proof-carrying code (Necula, *Proof-Carrying Code*,
POPL 1997; Necula and Lee, *Safe Kernel Extensions Without Run-Time Checking*,
OSDI 1996). There an untrusted code producer ships a program together with a
machine-checkable proof that it obeys a safety policy, and the consumer runs a
small trusted checker instead of trusting the producer. Trust moves from the
producer to the checker and the policy.

`clauz3` inherits that shape: the agent is the untrusted producer, the contract
plays the role of the policy, and the discharged proof is what the user relies
on rather than the agent's prose. Two differences are worth stating:

- Classic PCC fixes the policy on the consumer side and checks code against it.
  Here the agent *proposes* the contract, and the user accepts, rejects, or asks
  for a stronger one; the permission request and the policy are the same object
  (see "Operator Policy Versus Agent-Abduced Contracts" below).
- Classic PCC ships a proof and cheaply checks it; `clauz3` currently re-proves
  the program with a Z3-backed prover rather than checking a carried proof.
  Having the agent carry a checkable proof is a natural future direction.

The policy domain also differs: PCC targeted memory and type safety of running
code, while `clauz3` targets side-effect contracts (who is emailed, how much is
spent). The architecture — untrusted producer, trusted check, policy as the unit
of consent — is the same.

## FORGE

Palumbo, Choudhary, Choi, Amir, Chalasani, and Jha,
[Formal Policy Enforcement for Real-World Agentic Systems](https://arxiv.org/abs/2602.16708)
(2026), introduces FORGE, a runtime reference-monitor architecture for agentic
systems.

The shared starting point is similar: prompt-level policy instructions do not
provide enforcement guarantees, and the trust boundary between the agent and
the outside world should be explicit.

The mechanism differs:

- FORGE policies are written in Datalog over abstract predicates that describe
  execution context.
- A reference monitor intercepts policy-relevant actions and decides whether
  they may execute.
- An observability service maintains causal and provenance facts across tool
  calls, tool results, messages, and agents.
- The deployment is governed by an environment contract: enforcement is only as
  sound as the facts the environment promises to publish.

`clauz3` currently proves a single Python program before execution. FORGE
enforces individual decisions during execution. Those designs are not
substitutes. A static contract proof gives the user a concise permission
artifact and avoids partial side effects; runtime enforcement gives defense in
depth when execution drifts from the proved model.

## Operator Policy Versus Agent-Abduced Contracts

FORGE assumes policies are written by humans up front and applied to agents.
That is the right shape for organizational rules the agent should not be able
to weaken.

`clauz3` explores the opposite surface: the agent proposes a contract for a
specific program, the prover checks that program against that contract, and the
user accepts, rejects, or asks for a stronger claim. The agent may choose a weak
contract, but then it is visibly asking for broad permission.

This distinction is central to the approval UI. The user does not need to trust
the agent's natural-language explanation of what the code does; the user
reviews the discharged contract.

## A Loose DocuSign Analogy

The consent surface this design suggests is loosely DocuSign-shaped rather
than permission-popup-shaped: the contract — not the program — is the artifact
the user reads and approves, the proof obligation plays the role of legal
review, and agent prose appears alongside but visually demoted, like a cover
letter on a signed document. The analogy is not exact — DocuSign documents are
negotiated between humans, not abduced by one party for the other — but it
captures something the permission-dialog framing misses. The
`approved_remember` decision type in the
[approval-dialog sketch](../todos/user-approval-dialog.md) is closer to a limited
power of attorney than a remembered permission, and probably wants that
gravity.

## Environment Contract

The trusted layer is an environment contract. It says what the prover is
allowed to assume about the outside world.

For now, the environment contract is expressed by small Python modules under
trusted roots such as `tools/email/trusted/`:

- their signatures define effect fields
- their `@deal.pre(...)` decorators define required preconditions
- their `@deal.has(...)` markers identify trusted effect boundaries
- their `@contract` helpers define trusted domain contract vocabulary

If a trusted stub lies, the proof result is only as good as that lie. Keeping
this layer small and auditable is central to the design.

## Mechanics Worth Borrowing

FORGE gives names and mechanisms to several ideas that fit this project.

Environment contract:

The phrase is better than just "trusted stubs." It makes the assume/guarantee
boundary explicit: the prover assumes the stubs and markers mean what they say;
the environment must make that true.

Datalog frontend:

The current relation API is already closer to a declarative policy language
than the old solver-callback API:

```python
Email = effect("send_email")


@contract
def only(addresses: list[str]) -> ContractSpec:
    return Email.all(lambda e: e.addr in addresses)
```

A Datalog, SQL, or Prolog-like frontend could make these contracts easier to
author and audit, and could support static analyses such as contradiction,
redundancy, and subsumption checks.

Provenance facts:

The current prover records trusted calls as independent effect facts. It cannot
yet express policies like "this URL must have come from a literal allowlist or
from a trusted database table." A provenance relation in the fact layer would
unlock that class of policy.

Benchmarks:

The repo has examples by proof shape. It does not yet have an adversarial
benchmark. Even a small corpus of plausible agent-authored programs that try to
violate contracts would make prover coverage more measurable.

## What Is Distinct Here

Two properties remain specific to this design:

1. Static proof gives a binary go/no-go on the entire program before any trusted
   effect happens.
2. Contract abduction makes the agent propose the permission artifact, while the
   prover and user decide whether it is acceptable.

Those are the pieces worth preserving even if future versions borrow a Datalog
frontend, provenance facts, or runtime enforcement from related systems.
