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

### Does it sandbox the code or stop it doing arbitrary things?

Only within the modelled trusted layer. The prover reasons about declared
trusted effects and their preconditions; anything outside that layer is neither
modelled nor constrained. `clauz3` is not a sandbox or OS-level isolation, and
is not a substitute for one.

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

Both — it is a two-layer system. The *default* layer is **static**: the whole
program is accepted or rejected before any trusted effect runs, which avoids
partial side effects mid-transaction. Underneath it, the trusted layer's
[deal](https://deal.readthedocs.io/) contracts — `@deal.pre`, `@deal.post`,
`@deal.has` — are enforced at **runtime** on every call. The static prover
discharges those obligations *ahead* of execution; deal enforces the same
checks *at* execution. So when a contract is weak, unproven, or the program
runs in runtime-only mode, deal is still on guard at the trusted boundary.

The piece that genuinely is *not* implemented yet is binding the **approval
receipt** into that runtime boundary, so a trusted effect could refuse to run
without a valid receipt (see the README "Status" section). The earlier claim
that "there is no runtime enforcement" was wrong: deal's contract enforcement
is real and live. See [Concepts](concepts.md#static-proof-vs-runtime) for how
the two layers relate and [the email example](../examples/email.md#the-runtime-layer-deal-as-a-backstop)
for the runtime layer in action.

### How is this different from runtime guardrails or policy monitors?

Policy monitors intercept individual actions during execution and decide each
one against a policy; `clauz3` proves the whole program up front, so it can
reject before the first effect rather than blocking the second one
mid-transaction. This is *not* the same as the deal runtime layer above: deal
enforces fixed preconditions at the trusted boundary, it does not evaluate a
policy over the agent's actions. Static proof and policy monitors are
complementary, not rivals. See [background](background.md#forge).

### Is it production-ready?

No. The prover vendors and extends `deal-solver`, the contract language is
deliberately small, and runtime approval and receipts are design notes only. See
the README "Status" section for current limitations.

### What language does it support?

A subset of Python that the prover understands. See
[the Python subset](../reference/python-subset.md).

### How do I run it?

See the quickstart in the README and the worked cases under
[`examples/email`](../examples/email.md) and [`examples/bank`](../examples/bank.md).
