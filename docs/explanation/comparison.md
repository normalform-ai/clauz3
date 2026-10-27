# How ClauZ3 Compares

The one-line version:

> Sandboxes limit what code can touch. Guardrails guess. Policy monitors block
> mid-flight. ClauZ3 proves, before anything runs, a short list of promises you
> can read.

This page is the short answer. For the full literature comparison — proof-carrying
code, CaMeL, FORGE, Guardians, Dogwood, Cedar,
neuro-formal verification — see
[Background and related work](background.md).

## The problem everyone is solving

An agent wants to do something with real consequences: send email, move money,
edit files, order reagents. Today the choices usually look like this:

- **Click "Allow" on every tool call.** Safe in principle, but after the
  fortieth prompt nobody is reading them.
- **Grant the capability up front.** "This agent may use email" is one click,
  but it says nothing about *who* gets emailed or *how many times*.

ClauZ3 asks once, and asks a better question:

> This program will email only `bob@example.com`, at most once.
> **Approve?**

That sentence is not the agent's description of its own code. It is a
guarantee a Z3-backed prover has checked against *every* branch of the program
before anything runs.

## Why now: agents are writing programs

Agents increasingly accomplish tasks by writing a short program that calls
tools, rather than issuing one tool call at a time and reading each result.
Code is more compact, composes tools with loops and conditionals, and keeps
intermediate data out of the model's context.

That shift is exactly what makes static proof possible. A single tool call has
no structure to reason about; a program does. When the agent hands over a whole
program, you can ask what it will do on *every* path — before it takes any of
them. ClauZ3 is built for that input.

## At a glance

| Approach | When is it decided? | What does the user approve? | Can a half-finished run leave side effects? | Does the bound hold if the agent is manipulated? |
| --- | --- | --- | --- | --- |
| Per-call permission prompts | At each call | Each individual call | Yes — earlier calls already ran | Only if the user catches every bad call |
| Up-front capability grants / allowlists | Once, before the session | A whole tool or command | Yes | Only as far as "may use email" goes |
| Sandboxes (containers, VMs, seccomp) | At the OS boundary | Which resources are reachable | Yes | Yes, for what the sandbox blocks — but it cannot tell a good email from a bad one |
| LLM-based guardrails | At each call or message | Nothing explicit | Yes | Probabilistically — a model is in the trust path |
| Runtime policy monitors ([Cedar](cedar.md), [Dogwood](background.md#dogwood), [FORGE](background.md#forge)) | At each call, mid-run | An operator's policy, written in advance | Yes — the monitor blocks the *second* action | Yes, for what the policy covers |
| Information-flow interpreters ([CaMeL](background.md#camel)) | At each call, mid-run | An operator's policy | Yes — CaMeL's authors say so | Yes, and it also tracks where data came from |
| Plan verification ([Guardians](background.md#guardians)) | Once, before running | An operator's policy | No | Yes |
| **ClauZ3** | **Once, before running** | **A few readable guarantees for *this* program** | **No** | **Yes — the proof covers every branch** |

These approaches stack; they are not rivals. ClauZ3 does not replace a sandbox,
and a runtime monitor is a sensible second line of defence behind it.

## What ClauZ3 does that the others don't

1. **All-or-nothing, before anything runs.** A monitor that blocks the second
   payment cannot un-send the first. ClauZ3 accepts or rejects the whole
   program before the first trusted effect happens.
2. **The user approves promises, not code.** "Spends at most $500, only from the
   card account" is something a non-expert can read and agree to. The code is
   still there for anyone who wants it, but the proof is what they rely on.
3. **The bound survives a manipulated agent.** Suppose an injected email steers
   which branch the program takes. The proof already covered every branch, so no
   branch can spend past `bank.max_spend(500)`. The agent can still be *talked
   into asking* for a weak guarantee — but a weak guarantee is visible before
   you approve it, never silent.
4. **Plain Python, ordinary signatures.** The agent writes ordinary Python;
   effects are inferred from the signatures of small, audited trusted
   functions. There is no workflow DSL for the agent to learn and no separate
   tool schema to keep in sync.
5. **No model in the trust path.** The check is a deterministic Z3 proof. "The
   AI graded its own homework" is not a failure mode.

## What ClauZ3 does not do

Being clear about the gaps is part of the pitch:

- **It is not a sandbox.** It restricts what agent code may import and routes
  every real effect through a trusted layer, but it does not isolate a process.
  Run it inside one.
- **It does not stop the agent being fooled.** It limits what a fooled agent's
  program can do, and makes weak requests visible.
- **Guarantees are per program.** Five programs that each provably spend at most
  $500 spend $2,500 together. Budgets across a session or across time need a
  stateful layer such as [Dogwood](background.md#dogwood).
- **No confidentiality or value-level provenance yet.** "This secret must not
  reach anyone who cannot already read it" is CaMeL's territory, not ClauZ3's.
- **No ordering of effects.** Effects are an unordered set;
  [fluents](../reference/fluents.md) recover end-state properties such as "every
  door is locked at the end," but not "A before B" or time windows.
- **The trusted layer is the root of trust.** If a trusted function lies about
  its effects, the proof inherits the lie. Keeping that layer small and audited
  is the whole job.

## Where to go next

- [Quickstart](../quickstart.md) — see an approval end to end.
- [Concepts](concepts.md) — actors, trust, and static proof vs runtime.
- [Background and related work](background.md) — the long-form comparison.
- [FAQ](faq.md) — short answers to the usual objections.
- [Risk taxonomies](risk-taxonomies.md) — where ClauZ3 sits in published AI and agent risk taxonomies, with a formal coverage model.
