# FAQ

Short answers. For the concepts behind them see [Concepts](concepts.md); for
related work and design rationale see [background](background.md).

### What is `clauz3`?

An experimental static contract layer for agent-authored Python. An agent writes
a short program plus a contract describing its trusted side effects, and a
Z3-backed prover checks the contract before anything runs. It is a research
experiment, not a finished product.

### Does this prevent prompt injection or other prompt exploits?

No. `clauz3` reasons about what a *proven program* can do; it has no opinion on
how the agent was talked into writing that program. A manipulated agent can
still propose a program with a weak — but honest — contract. The point is that
the weakness is visible before you approve, not that the agent cannot be fooled.

What a discharged contract *does* give you is a bound that survives a
manipulated agent: the proof covers every branch, so untrusted content steering
which branch runs cannot push the program past the effects the contract allows.
That bound is conditional on the trusted layer being honest about its effects —
a stub that under-declares voids it, as with any other guarantee here (see
[below](#what-if-a-trusted-stub-is-wrong-or-lies) and the
[environment contract](background.md#environment-contract)).
The system that attacks the injection problem head-on, by tracking provenance on
every value, is CaMeL — see [background § CaMeL](background.md#camel) for how the
two compare, and
[Defend against malicious repos](../how-to/malware-repo-defense.md) for the
data-flow contracts that come closest here.
[Instructions are not controls](instructions-and-contracts.md) walks through
the research on indirect injection and where a contract bounds it.

### Does it sandbox the code or stop it doing arbitrary things?

`clauz3` is not a sandbox or OS-level isolation and is not a substitute for
one. Within its trust model, though, the agent cannot route around the
trusted layer: untrusted source is checked against a positive import
allowlist before the prover or runner accept it, so `import os` (or
`subprocess`, `socket`, `pickle`, `requests`, …) is rejected. The expected
route for those capabilities is a trusted effect, where their preconditions
become proof obligations. See
[Concepts → Static proof vs runtime](concepts.md#static-proof-vs-runtime)
and the
[import allowlist reference](../reference/python-subset.md#import-allowlist).

### Is the agent trusted?

No. Everything the agent writes — code and guarantees — must be discharged by
the prover. The trusted parts are the trusted layer (written by the Trusted
Layer Engineer) and the prover itself.

### Does an LLM check the contract?

No. The check is a deterministic Z3-backed proof. No model sits in the trust
path, so "the AI graded its own homework" is not a failure mode here.

### Do I have to read the code?

No. You review the contract — a short, checked statement like "every email goes
to an address in this list." The code is available if you want it, but the proof
is what you rely on.

### What if a trusted stub is wrong, or lies?

Then the proof is only as good as that stub. The trusted layer is the root of
trust by design; keeping it small and audited is the whole job. `clauz3` moves
trust from the agent to a small audited layer — it does not abolish trust.

### Can the agent just give a weak contract?

Yes — and then it is visibly asking for broad permission. Weakness is allowed
but not silent. `emails.no_guarantees()` is the explicit null contract: it
promises nothing, but it has to say so out loud.

### Is this static or runtime?

Both — it is a two-layer system. `clauz3 run` first requires a **static**
proof: the whole program is accepted or rejected before any trusted effect runs, which avoids
partial side effects mid-transaction. Underneath it, the trusted layer's
conditions — `@clauz3.requires`, `@clauz3.ensures` — are enforced at
**runtime** on every call. The prover checks call-site preconditions and
assumes trusted postconditions; runtime checks enforce them when the function
runs. `no_guarantees()` leaves effects unconstrained, but still requires those
preconditions to hold. Direct calls outside `clauz3 run` also retain the
runtime checks.

A trusted layer can add a further runtime layer about its *own bodies* —
which side effects each one may have — with
[deal](../how-to/deal.md), which is also what `clauz3 lint` checks.

The piece that genuinely is *not* implemented yet is binding the **approval
receipt** into that runtime boundary, so a trusted effect could refuse to run
without a valid receipt (see the README "Status" section). The earlier claim
that "there is no runtime enforcement" was wrong: the contract enforcement is
real and live. See [Concepts](concepts.md#static-proof-vs-runtime) for how the
two layers relate and [the email example](../examples/email.md#the-runtime-layer)
for the runtime layer in action.

### How is this different from runtime guardrails or policy monitors?

Policy monitors intercept individual actions during execution and decide each
one against a policy; `clauz3` proves the whole program up front, so it can
reject before the first effect rather than blocking the second one
mid-transaction. This is *not* the same as the runtime layer above: that
enforces fixed preconditions at the trusted boundary, it does not evaluate a
policy over the agent's actions. Static proof and policy monitors are
complementary, not rivals. See [How ClauZ3 compares](comparison.md) for a
one-page table, and [background](background.md#forge) for the detail.

### Is it production-ready?

No. The prover vendors and extends `deal-solver` and the contract language is
deliberately small. `clauz3 run` does require a signed approval receipt before
executing, but trusted effects do not yet check that receipt themselves, and
`run` is not a hardened sandbox. See the README "Status" section for current
limitations.

### What language does it support?

A subset of Python that the prover understands. See
[the Python subset](../reference/python-subset.md).

### How do I run it?

See the quickstart in the README and the worked cases under
[`examples/email`](../examples/email.md) and [`examples/bank`](../examples/bank.md).
