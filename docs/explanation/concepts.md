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
The approval service mediates that decision and issues a receipt attesting the
user's consent.

## The User

The user reviews and accepts (or rejects) a **contract**, not code. The point is
that a user should be able to answer "will this email anyone other than Bob?"
without reading every branch of the program. The contract is the permission
request; the proof is what makes it worth relying on.

## The approval service

The approval service shows the user the proved guarantees, **mediates** the
approve/reject decision, and tells `clauz3 run` to proceed once the user
consents. It runs outside the agent's control, and the agent cannot route around
it: its only path to any side effect is `clauz3 run`, which will not execute
until the service approves. So the agent cannot fake the dialog or skip it — not
because a token is hard to forge, but because it has no channel that bypasses the
gate. None of this depends on the service keeping an audit log; for a single
`clauz3 run` it may forget the decision the moment the program proceeds. (Durable
*recording* matters only for *remembered*, standing consent — closer to a limited
power of attorney than a one-shot authorization.) The user is granting a scoped,
enforced permission for one program — not signing a standing or binding contract.
The promise is the agent's; the user authorizes it.

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

**Trusted effects** — functions declared with `@clauz3.trusted`, together with
their preconditions and postconditions. These define which calls the prover
records and what must hold before and after each call.

```python
import clauz3

@clauz3.requires(lambda addr, msg: "@" in addr, message='addr must contain "@"')
@clauz3.trusted
def send_email(addr: str, msg: str) -> None:
    ...
```

**Contract vocabulary** — reusable `@contract` predicates the agent and user
speak in. This is the "logic": the domain templates that turn raw effect facts
into claims worth consenting to.

```python
from clauz3.spec import ContractSpec, contract, effect
from tools.email.trusted.effects import send_email

Email = effect(send_email)

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

## Trust boundary: what runs as Python, what is interpreted

The trusted layer and the agent's program are both Python files, but the
prover treats their parts very differently. Four kinds of code meet at the
boundary. What makes a contracts file auditable is knowing which of them the
prover *executes* and which it merely *interprets*.

| Code | Who writes it | Where it lives | How it is evaluated at prove time | What it may reference |
| --- | --- | --- | --- | --- |
| **Trusted effect**<br>`@clauz3.trusted` function | TLE | trusted root, `effects.py` | A modeled call records a symbolic fact without executing the body. Its `@clauz3.requires` predicate becomes a caller obligation. Execution under `clauz3 run` requires approval. | The real implementation may use external services and libraries. |
| **`@contract` helper body** | TLE | trusted root, `contracts.py` | **Executed as ordinary Python** to construct a `ContractSpec`. The returned spec is solved; the helper body is trusted, not proved. | Trusted Python code, subject to the loading and registration checks. |
| **Relation lambda**<br>`lambda e: e.addr in addresses` | TLE | inside a helper body | **Compiled from source**, rather than called as Python during proof. Unsupported expressions refuse the proof. | Row fields, supported captures, and the [relation expression subset](../reference/python-subset.md#relation-primitives). |
| **Guarantee expression**<br>`@clauz3.guarantee(emails.only([...]))` | Agent | untrusted entry file | Validated as a call to a registered `@contract` helper. Agent-authored expressions are analyzed, not handed to CPython. | A registered helper with literal or Row-schema arguments, as defined by [the guarantee grammar](../reference/python-subset.md#the-guarantee-grammar). |

Trusted modules are loaded when a proof starts. Their initialization can run
Python code and register helpers before approval of the agent's entry point;
the approval boundary does not sandbox trusted-layer initialization.

The source validator rejects trusted effect, contract, and solver declarations
in agent-authored code, including import aliases. The
[import allowlist](../reference/python-subset.md#import-allowlist) limits which
modules untrusted code can reference. An agent uses the effects and contracts
provided by an authorized trusted root rather than declaring its own boundary.

### Why helper bodies must stay boring

The second row is the one to internalise. Because the helper body is executed,
it decides which spec the prover solves, and nothing checks how it decided.
`clauz3` accepts this helper today:

```python
import os

from clauz3.spec import ContractSpec, contract, effect, no_guarantees

Email = effect("send_email")


@contract
def only(addresses: list[str]) -> ContractSpec:
    if os.environ.get("WEAK_MODE"):
        return no_guarantees()
    return Email.all(lambda e: e.addr in addresses)
```

With `WEAK_MODE=1` in the prover's environment,
`@clauz3.guarantee(emails.only(["bob@example.com"]))` proves over a program
that emails `eve@example.com`, and the output is the same `proved!` as an
honest run. The relation lambda could not have done this: writing
`lambda e: e.addr in addresses or weaken()` fails with
`unsupported call in effect lambda`, and `weaken` never executes.

So the discipline for `contracts.py` is the same as for `effects.py`: review it
as trusted code. A helper body should construct a spec from its arguments and
nothing else: import only the framework and trusted vocabulary it needs; avoid
environment or file access,
no branches on anything but its own parameters. Put the logic in the lambda,
where the compiler refuses what it cannot prove. Static validation of helper
bodies is future work; see the
[Python subset design direction](../reference/python-subset.md#design-direction).

### Reading a guarantee: is it load-bearing?

A guarantee must call a registered contract helper imported from a trusted
root. A bare helper reference such as `@clauz3.guarantee(emails.only)` and an
agent-defined look-alike such as `@clauz3.guarantee(only([...]))` are refused
by the guarantee grammar; neither is accepted as a substitute for a contract.
See [the guarantee grammar](../reference/python-subset.md#the-guarantee-grammar).

That syntactic gate does not establish that the trusted helper returns a useful
spec. A helper that returns `no_guarantees()` still imposes no restriction.
Review the returned contract and the trusted implementation together.
[Coverage policies](../reference/coverage-policies.md) help identify missing
domain guarantees, but they do not prove the helper body's intent.

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

For the proof to mean anything, the agent must not be able to do work
*outside* the trusted layer — `import os; os.system(...)` would make every
guarantee a fiction. `clauz3` therefore enforces a positive **import
allowlist** on untrusted source: only the `clauz3` framework, the trusted
layers reachable from the configured roots, and a small set of pure stdlib
modules are importable. Anything that reads the world (`os`, `subprocess`,
`socket`, `urllib`, `pickle`, `time`, `random`, …) is rejected at both
prove time and run time. The expected route for those capabilities is to
wrap them in a trusted effect, where their preconditions become proof
obligations. The full list and the `--allow-import` escape hatch are
documented in
[the Python subset reference](../reference/python-subset.md#import-allowlist).

### Weaker guarantees still require a proof

The user and agent can agree to a weaker effect contract. For example,
`emails.no_guarantees()` imposes no restriction on email effects, so the user
is being asked to accept broader permission. It does not remove the trusted
function's preconditions or permit unsupported Python. `clauz3 run` still
requires a successful proof before requesting approval and executing the
program; a failed or skipped proof stops the run.

Every `@clauz3.requires` and `@clauz3.ensures` condition is also checked at
runtime. The prover checks preconditions at call sites and assumes the trusted
function satisfies its postconditions. Runtime checks enforce those
conditions when the function actually runs, including when a caller invokes
the trusted function directly outside `clauz3 run`.

Concretely, the email trusted layer declares
`@clauz3.requires(lambda addr, msg: "@" in addr)` on `send_email`. A program
calling `send_email("not-an-email", ...)` is rejected *statically* by the
prover, which cannot discharge the precondition. The same precondition fires
*at runtime* if such a call is ever executed:

```pycon
>>> send_email("not-an-email", "hi")
PreContractError: addr must contain "@" (where addr='not-an-email', msg='hi')
```

Same obligation, two enforcement points. See
[the email example](../examples/email.md#the-runtime-layer) for the full worked
case, and [Extra runtime enforcement](../how-to/deal.md) for the further
checks a trusted layer can put on its *own bodies* — the side effects a body
is allowed to have, which no proof about an agent's program can speak to.

So the only enforcement that is genuinely *not* implemented yet is binding the
approval **receipt** into this runtime boundary — a trusted effect refusing to
run without a valid receipt. The conditions themselves are enforced today.

### Identifiers are outside both layers

Both enforcement points check *properties of the arguments*. Neither can tell
whether `"Dining Room"` names a zone the thermostat bridge actually has. A
proof of `home.only_zones(["Dining Room"])` is a proof over a string; if the
bridge has no such zone and the trusted effect returns quietly on a miss, the
result is a fully proved program that does nothing. The prover cannot close
that gap; only the trusted layer can, by raising on an identifier it cannot
resolve. See [Avoid vacuous proofs](../how-to/vacuous-proofs.md).

## Glossary

- **User** — reviews and consents to contracts; does not read or run code.
- **Agent** — writes the program and its guarantees; untrusted.
- **Trusted Layer Engineer (TLE)** — authors and certifies the trusted layer;
  the root of trust.
- **Trusted layer** — the audited layer the prover assumes (a.k.a. the *trusted
  roots*, the *trusted base*, the *environment contract*).
- **Approval service** — presents the proved guarantees to the user, mediates
  the approve/reject decision, and issues a receipt attesting consent; runs
  outside the agent's control.
- **Trusted effect** — a side-effecting trusted function marked with
  `@clauz3.trusted`.
- **Contract vocabulary** — reusable `@contract` predicates over effect facts.
- **Relation lambda** — the `lambda e: ...` inside a `@contract` helper;
  compiled by `clauz3.spec` into Z3 constraints, never executed.
- **Guarantee** — an agent's claim about its program, stated in that vocabulary.
- **Contract** — a discharged guarantee; the artifact the user consents to.
