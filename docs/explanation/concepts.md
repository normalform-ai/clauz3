# Concepts

How `clauz3` splits responsibility, and what a contract actually means. This is
the understanding-oriented companion to the [FAQ](faq.md) (quick answers) and
[background](background.md) (related work and design rationale).

## Actors and trust

Three roles, plus the approval service that sits between them. They differ
mostly in how much they are trusted.

| Role | Writes | What they are trusted for |
| --- | --- | --- |
| **User** | nothing | the final decision; consents to a contract, never reads or runs code |
| **Agent** | an ad-hoc program plus `@clauz3.guarantee(...)` claims about it | nothing; everything it writes must be proved |
| **Trusted Layer Engineer (TLE)** | the trusted layer: side-effecting functions *and* the `@contract` vocabulary | everything; this is the root of trust |

The split mirrors classic assume/guarantee reasoning. The TLE defines the
**assumptions** (what the prover may take for granted about the outside world).
The agent states **guarantees** about a specific program. The prover checks that
the guarantees follow from the assumptions, and the user consents to the result.
The approval service presents that result and records the decision.

## The User

The user reviews and accepts (or rejects) a **contract**, not code. The point is
that a user should be able to answer "will this email anyone other than Bob?"
without reading every branch of the program. The contract is the permission
request; the proof is what makes it worth relying on.

## The approval service

The approval service shows the user the proved guarantees and records the
decision to approve or reject. It runs outside the agent's control, so the agent
cannot fake the dialog or skip it. The user is granting a scoped, enforced
permission for one program — not signing a standing or binding contract. The
promise is the agent's; the user authorizes it.

Operationally, `clauz3 run` proves the program first, submits a JSON approval
request only after the proof succeeds, and executes the target only after the
service returns an approved decision with a receipt. The bundled local service
is configured by the user or harness and can be inspected through a browser UI.
See [Approval service](../how-to/approval-service.md) for startup, URL discovery,
command-line arguments, routes, and request/response shape.

## The Agent

The agent writes a small Python program against trusted functions and attaches
guarantees to `main`:

```python
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    send_email("bob@example.com", "hi")
```

The agent is **untrusted**. It may propose a weak contract, but weakness is
visible (see `no_guarantees` in the [FAQ](faq.md)). Nothing the agent writes is
believed until the prover discharges it.

## The Trusted Layer Engineer (TLE)

The TLE authors and certifies the **trusted layer** — the small, audited layer
the prover is allowed to assume. This is the role behind the modules under
`tools/<domain>/trusted/`. It can be a human or done agentically, but it is the
security-critical role: if this layer is wrong, the proof is wrong.

They author two halves.

**Trusted effects** — the side-effecting functions and their markers. These
define what an effect *is* and what must hold before it runs.

```python
import deal

@deal.pre(lambda addr, msg: "@" in addr, message='addr must contain "@"')
@deal.has("trusted")
def send_email(addr: str, msg: str) -> None:
    ...
```

**Contract vocabulary** — reusable `@contract` predicates the agent and user
speak in. This is the "logic": the domain templates that turn raw effect facts
into claims worth consenting to.

```python
from clauz3.spec import ContractSpec, contract, effect

Email = effect("send_email")

@contract
def only(addresses: list[str]) -> ContractSpec:
    return Email.all(lambda e: e.addr in addresses)
```

## Guarantee vs. contract vs. environment contract

These words are easy to conflate. They are not the same thing:

- **Contract vocabulary** — the reusable predicates (`emails.only`,
  `bank.max_spend`, …) authored by the TLE. The shared language.
- **Guarantee** — a specific claim the agent attaches to its program using that
  vocabulary, e.g. `@clauz3.guarantee(emails.only(["bob@example.com"]))`.
- **Environment contract** — the trusted layer as a whole, viewed as a promise
  about the outside world. It says what the prover may assume.

In short: the TLE defines the vocabulary and the environment contract; the agent
writes guarantees; the user consents to a proved guarantee.

## Why the contract is the unit of consent

The contract is small; the code is not. A program can branch many ways, but the
discharged contract is a single checkable statement like "every email goes to an
address in this list." The user does not have to trust the agent's prose about
what the code does — the prover has already shown the code cannot violate the
contract. The lineage for this idea is proof-carrying code; see
[background](background.md#proof-carrying-code).

## Static proof vs runtime

`clauz3` accepts or rejects the **entire** program before any trusted effect
runs. This avoids the partial-failure mode of runtime monitors, where the first
email is already sent when the second is blocked mid-transaction. The cost is
that the prover must reason about all relevant branches ahead of time, and the
contract language has to stay inside a tractable subset.

### Falling back to runtime is allowed

Static proof is the *default* posture, not the only one. Some agent tasks are
genuinely beyond what any tractable static subset can prove: programs with
unbounded loops over data the prover has no schema for, calls into untyped
third-party services, generated SQL, ad-hoc string manipulation. Forcing every
such task through a static layer is a recipe for the prover-as-bottleneck —
the agent either rewrites the problem until it fits, or gives up.

The philosophy here is that the user and the agent can agree to a weaker
posture. If the agent declares `emails.no_guarantees()`, or explicitly leaves
a domain unconstrained in its declared coverage, and the user accepts that
contract, that is a *shared decision* the system records and honors. The user
sees that no static guarantee exists for the relevant effects, and is
accepting the runtime-only mode — including partial-failure modes like the
first email going out when the second one trips a runtime precondition. We
don't get in the way of that agreement.

What does *not* go away in runtime-only mode is the trusted layer's own
runtime checks. Every trusted effect is still wrapped in
[deal](https://deal.readthedocs.io/) preconditions (`@deal.pre`), postconditions
(`@deal.post`), and effect markers (`@deal.has`). The static prover treats
these as proof obligations *ahead* of execution; in runtime-only mode, deal
enforces them *at* execution. Same checks, different time. The trusted
layer is the same artifact; only the user's acceptance changes.

This is why clauz3 layers cleanly on deal rather than replacing it. deal is
the runtime contract engine; clauz3 reads the same decorator vocabulary and
discharges those checks statically against an agent-authored program. If the
proof succeeds, the deal checks at runtime are redundant (but still safe). If
the proof is skipped or the contract is intentionally weak, deal is still on
guard at the trusted boundary.

Concretely, the email trusted layer declares `@deal.pre(lambda addr, msg: "@"
in addr)` on `send_email`. A program that calls `send_email("not-an-email",
...)` is rejected *statically* by the prover (it cannot discharge the
precondition). The exact same precondition fires *at runtime* if such a call is
ever executed:

```pycon
>>> send_email("not-an-email", "hi")
deal.PreContractError: addr must contain "@" (where addr='not-an-email', msg='hi')
```

Same obligation, two enforcement points. See
[the email example](../examples/email.md#the-runtime-layer-deal-as-a-backstop)
for the full worked case.

So the only enforcement that is genuinely *not* implemented yet is binding the
approval **receipt** into this runtime boundary — a trusted effect refusing to
run without a valid receipt. The deal preconditions, postconditions, and effect
markers themselves are enforced today.

## Glossary

- **User** — reviews and consents to contracts; does not read or run code.
- **Agent** — writes the program and its guarantees; untrusted.
- **Trusted Layer Engineer (TLE)** — authors and certifies the trusted layer;
  the root of trust.
- **Trusted layer** — the audited layer the prover assumes (a.k.a. the *trusted
  roots*, the *trusted base*, the *environment contract*).
- **Approval service** — presents the proved guarantees to the user and records
  the approve/reject decision; runs outside the agent's control.
- **Trusted effect** — a side-effecting trusted function marked with
  `@deal.has(...)`.
- **Contract vocabulary** — reusable `@contract` predicates over effect facts.
- **Guarantee** — an agent's claim about its program, stated in that vocabulary.
- **Contract** — a discharged guarantee; the artifact the user consents to.
