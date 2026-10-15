# Background And Related Work

`clauz3` is a static contract layer for agent-authored Python. The agent
writes a short program and proposes a side-effect contract over it; a Z3-backed
prover either discharges the contract or rejects it; the discharged contract is
the artifact the user consents to. Runtime execution is then all-or-nothing.
There is no per-call gate in the current design.

This is different from most agent permission systems, which either grant broad
capabilities up front or intercept individual tool calls at runtime.

This page is the long-form comparison. For a one-page summary aimed at a
general audience, see [How ClauZ3 compares](comparison.md).

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

## CaMeL

Debenedetti, Shumailov, Fan, Hayes, Carlini, Fabian, Kern, Shi, Terzis, and
Tramèr, [Defeating Prompt Injections by Design](https://arxiv.org/abs/2503.18813v2)
(Google DeepMind and ETH Zurich; code at
[`google-research/camel-prompt-injection`](https://github.com/google-research/camel-prompt-injection);
[Simon Willison's write-up](https://simonwillison.net/2025/Apr/11/camel/) is the
short version), introduces **CaMeL** — CApabilities for MachinE Learning.

The link is pinned to arXiv **v2** (June 2025); every quotation, figure, and
section number below is from that revision.

It is the nearest neighbour of this project on the axis that matters most here:
the agent does not run a tool-calling loop, it **writes a program**, and the
security layer reasons about the program rather than about the model. CaMeL
arrives at that from information-flow control; `clauz3` arrives at it from
proof-carrying code. That two designs with unrelated ancestries converge on
"agent-authored code in a restricted Python subset, interpreted by something
that is not CPython" is the most interesting thing in the comparison.

The architecture has five parts:

- a **Privileged LLM (P-LLM)** that sees only the user's query and emits Python
  expressing it. Tool results live in variables it never reads; the only thing
  it sees after writing the code is the code.
- a **Quarantined LLM (Q-LLM)** that parses untrusted text into a declared
  Pydantic schema and has no tool access. CaMeL is, to the authors' knowledge,
  the first concrete instantiation of Willison's Dual-LLM pattern. The schema is
  extended with a `have_enough_information` boolean; when it comes back false
  the interpreter raises `NotEnoughInformationError`. Notably, the Q-LLM
  *cannot tell the P-LLM what it was missing* — that channel would itself be an
  injection vector.
- **capabilities**: tags on every value recording data provenance (`User` for
  literals in the P-LLM's code, `CaMeL` for computed values, or a specific tool,
  optionally with an inner source such as an email's sender) and a set of
  allowed **readers** (`Public`, or a set of users).
- **security policies**: ordinary Python functions, `(tool, args) ->
  Allowed | Denied`, deliberately not a DSL, evaluated before each tool call
  against the argument's transitive dependency graph.
- a **custom Python interpreter** that walks the AST, propagates capabilities,
  maintains the data-flow graph, and enforces the policies. Its `STRICT` mode
  adds a block's test or iterable as a dependency of everything assigned inside
  it, so implicit flows are tracked too.

The headline result in v2 is 77% of AgentDojo tasks solved with provable
security, against 84% for the undefended agent — up from the 67% reported in v1,
which quoted no undefended baseline. The paper credits that rise to newer
backbone models rather than to changes in CaMeL: utility "significantly improved
in a second round of experiments where we employed more recent models." The
figure is not really the point. What is, is what the security rests on: "the
untrusted data retrieved by the LLM can never impact the program flow" —
structural rather than probabilistic.

### How it lines up

|  | CaMeL | `clauz3` |
| --- | --- | --- |
| Agent surface | Python subset emitted by the P-LLM | Python subset written by the agent |
| Executed by | custom interpreter, capabilities propagated per value | `clauz3.interp`, the same shape of interpreter, after a proof |
| Decided | before each tool call, on concrete values and their dependency graph | once, before the program runs, symbolically over every branch |
| Policy author | the engine (operator), as Python predicates | the agent proposes; the user consents |
| Property enforced | provenance and readership of each value (integrity *and* confidentiality) | relational bounds on the effect set (who, how many, how much) |
| Rejection unit | the individual tool call, mid-run | the whole program, before anything runs |

Where CaMeL is ahead, and `clauz3` is not:

- **Value-level provenance.** A capability travels with the value through
  arbitrary computation. `clauz3`'s provenance is *structural*: a contract can
  say an argument is symbolically the `asset_url` column of a queried row
  ([`examples/gh-triage`](../how-to/malware-repo-defense.md)) or that a
  recipient is `UserRow.email` from a trusted query
  ([symbolic iteration](symbolic-iteration.md)), which pins the shape of a data
  path the prover can see. It is not a label that survives being sliced,
  concatenated, and stuffed into a dict.
- **Confidentiality.** The reader set gives CaMeL exfiltration policies —
  "this may not be sent to anyone who cannot already read it." `clauz3` has no
  notion of an observer at all; every contract it can state is about what the
  program *does*, never about who learns what.
- **Untrusted data as an input.** The Q-LLM is a principled way to *use*
  attacker-controlled text: parse it under a schema, keep the label. `clauz3`
  has no counterpart; untrusted content enters as an opaque symbol from a
  trusted read and can only be constrained by where it flows.

Where `clauz3` is distinct, and the difference is not cosmetic:

- **No partial side effects.** The paper is candid: "At present CaMeL has no
  means to enable atomicity for sequences of instructions or rollbacks of
  operations that failed yet had side-effects." Its repair loop makes this
  concrete — when the interpreter re-prompts the P-LLM after an exception, the
  model is told to assume the code up to that point *already ran*. This is the
  same transactional argument for proving before executing that
  [static proof vs runtime](concepts.md#static-proof-vs-runtime) makes against
  reference monitors generally; CaMeL is a per-call monitor in this respect,
  and says so.
- **The consent artifact.** CaMeL's policies are engine-defined, and its
  discussion of what remains unsolved leads with the cost: users "need to codify
  and specify security policies and maintain them," with de-classification and
  user fatigue as the standing risk. `clauz3` bets on the other surface — the
  agent abduces a contract for *this* program, and the user approves the
  contract rather than a stream of per-call prompts (see "Operator Policy Versus
  Agent-Abduced Contracts" below).
- **What survives "data flow becomes control flow."** CaMeL's §6.4 case study
  is a P-LLM that, asked to do whatever an email says, writes a dispatcher
  iterating over the available tools and picking one from the email's content;
  attacker data then chooses the branch, and isolation alone does not help. A
  proof quantifies over *every* branch, so branch selection by attacker content
  is not by itself an escape: a dispatcher over all tools cannot discharge
  `emails.only([...])` or `bank.max_spend(500)`, because some branch violates
  it. The bound the user approved holds however the program is steered. Two
  caveats keep this honest — it holds only up to the
  [environment contract](#environment-contract), and the agent remains free to
  propose a program whose contract is *weak*, which is visible rather than
  prevented (see the [FAQ](faq.md#does-this-prevent-prompt-injection-or-other-prompt-exploits)).

And one axis where CaMeL is simply looking at something `clauz3` cannot see.
Section 7 catalogues side channels: an image fetched a number of times that
depends on a private value, one bit leaked by whether an attacker-triggered
exception halted the run, timing. `STRICT` mode exists for the first two.
`clauz3`'s effect facts do carry a path condition, which looks superficially
like a control dependency, but it is there for reachability — "under what
condition does this effect happen" — not for leakage. With no observer in the
model there is no leak to state, let alone bound.

### Where they compose

The pairing is unusually clean, because `clauz3` now owns its interpreter:
`clauz3.interp` runs the proved subset directly instead of handing it to CPython
(see [subset-interpreter](../todos/subset-interpreter.md)). Capabilities are
metadata on values in exactly that kind of interpreter. A combined system would
prove the contract statically — giving the user one artifact to consent to, and
no partial effects — and then propagate CaMeL-style labels while running,
denying the value-level flows the proof abstracted away. That is the
"prove statically, enforce at runtime" shape this document opens with, with
CaMeL supplying a worked design for the runtime half rather than a sketch.

The label design is also the most concrete answer available to the provenance
gap noted under "Mechanics Worth Borrowing" below: a source tag plus a reader
set, propagated through assignment, with tools free to attach an inner source.
[Guardians synergies, Track 1](../todos/guardians-synergies.md#track-1-taint-and-provenance-in-the-fact-layer)
tracks where that would attach in the fact layer.

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

`clauz3` proves a single Python program before execution; that *static* proof
is its distinctive layer. FORGE-style policy enforcement decides individual
actions during execution. Those designs are not substitutes. A static contract
proof gives the user a concise permission artifact and avoids partial side
effects; policy-monitor runtime enforcement gives defense in depth when
execution drifts from the proved model.

Note this comparison is about *policy* enforcement, not about whether clauz3 has
any runtime checks at all — it does. The trusted layer's `@clauz3.requires`
and `@clauz3.ensures` conditions are enforced at runtime regardless of the
proof. Optional `@deal.has(...)` declarations add checks on the body's own
side effects (see
[Concepts: static proof vs runtime](concepts.md#static-proof-vs-runtime)). What
clauz3 lacks, relative to FORGE, is a reference monitor evaluating a policy over
the agent's actions as they happen.

## Guardians

Meijer, *Guardians of the Agents: Formal verification of AI workflows*
([ACM Queue 23(4), July–August 2025](https://queue.acm.org/detail.cfm?id=3762990);
Communications of the ACM, January 2026;
[doi:10.1145/3777544](https://doi.org/10.1145/3777544)), proposes a
**generate → verify → execute** discipline for agents, with an open-source
implementation at
[`metareflection/guardians`](https://github.com/metareflection/guardians).
Meijer frames it as "a safety paradigm rooted in mathematical proof
verification" and an extension of Java/.NET **bytecode verification** to agentic
computation — the same "complexity in production, verification stays simple"
asymmetry that motivates proof-carrying code above.

The core argument is that prompt injection has the same root cause as SQL
injection: code (instructions) and data (content) are not separated, so the fix
is the same. Instead of letting the model call one tool, read the result, and
then decide the next side-effecting action, the model emits a complete
structured **workflow** *up front* — a JSON AST of steps whose arguments are
**symbolic references** (string result bindings such as `"emails_fetched"`,
rendered `@emails_fetched` in the literate explanation; modeled as `SymRef` in
the implementation) rather than concrete values. The plan is authored from the
goal and tool specs alone, before any concrete (possibly attacker-controlled)
data exists, then verified against a security policy, and only then executed.
Because the code is fixed before the data arrives, malicious content in inbox
data, tool results, *or tool descriptions* cannot introduce a new
side-effecting step; anything unexpected is caught at verification and the
workflow is rejected.

The paper's running example is an email exfiltration: a malicious inbox message
instructs the agent to silently forward a summary to `it@othercorp.com`.
Verification draws on:

- **Source-sink / taint analysis** — a policy forbidding data flow from
  `fetch_email`'s result to `send_email`'s `body` when the `to` argument is
  outside an allowlist. Meijer suggests a CodeQL path query or SemGrep; the
  implementation carries this as its own `TaintRule` with provenance labels and
  sanitizers.
- **Security automata** — a finite state machine over the tool-call sequence
  that rejects on reaching an error state (the paper's figure 2 forbids sending
  to external domains). Meijer introduces this as a *runtime* monitor; the
  implementation also evaluates it at verification time.
- **Z3 / Dafny over pre/post/frame conditions** — including a careful treatment
  of the **frame problem** (McCarthy & Hayes, 1969): a naive postcondition for
  "delete foo.txt and bar.txt" is also satisfied by `delete_file("*.txt")`, so a
  **frame condition** ("files not matching the pattern are unchanged") is needed
  before the over-broad plan fails to verify.

Meijer gives three reasons to verify first: prevention rather than detection,
**eliminating the need for rollbacks** (only verified workflows run, so there is
no partial side effect to undo), and automation. The middle reason is the same
transactional argument `clauz3` makes for static proof over runtime monitors
(see [static proof vs runtime](concepts.md#static-proof-vs-runtime)).

The shared ground with `clauz3` is substantial: both reject unsafe behavior
*before* side effects run, both make the tool/effect trust boundary explicit,
both compile to Z3, and both replace ad-hoc natural-language promises with a
checkable artifact. The mechanism differs:

- Guardians verifies a structured **workflow AST** against an operator-defined
  policy, with the plan fixed before data arrives.
- `clauz3` proves an agent-authored **Python program** satisfies
  agent-stated guarantees over trusted effect facts derived by symbolic
  execution, and treats the proved contract as the user-facing consent artifact.

Where Guardians is ahead, and `clauz3` is not yet:

- a first-class symbolic workflow representation, with the clean code/data
  separation that gives the prompt-injection story;
- built-in taint/provenance tracking for source-to-sink data flow;
- built-in security automata for tool-call *sequences*.

One difference cuts the other way. Meijer needs explicit frame conditions
because his Z3 obligations describe world state (`fileSystem`), and a model will
satisfy an under-specified postcondition the cheapest way it can. `clauz3`
reasons instead over a *closed-world finite trace* of effect facts — an effect
not in the trace provably does not happen — so contracts like
`filesystem.only_write_under` or `emails.only` bound the entire effect set
directly, giving the frame guarantee implicitly for the effects the prover
tracks (see
[effect-IR](../todos/effect-ir.md#what-level-fol-datalog-or-the-z3-lisp-syntax)).

`clauz3` does already reason about data it has not seen — its symbolic
iteration over a trusted query's `list[Row]` return, with `UserRow.email`
column-binding contracts (see
[symbolic iteration](symbolic-iteration.md)), is the closest existing analog to
"plan before the data." What it lacks is the provenance layer needed to say "a
value that came from `fetch_mail` must not reach `send_email.body`," and any
notion of effect *order* on which an automaton could run; today the effect facts
are an unordered set carrying only path conditions.

Where `clauz3` is distinct:

- ordinary Python as the agent-authored surface, rather than a bespoke workflow
  AST;
- effect facts inferred from trusted function signatures, rather than
  hand-declared `ToolSpec`s;
- a user-facing contract vocabulary (allowlists, absence, uniqueness, counts,
  sums, filtered counts, joins) and an approval service built around proved
  guarantees and coverage, not pass/fail policy enforcement;
- contract *abduction* — the agent proposes the permission artifact (see
  "Operator Policy Versus Agent-Abduced Contracts" below).

These projects look more like complementary halves than alternatives. The
concrete synergy tracks — taint/provenance in the fact layer, security automata
over effect traces, a workflow front-end that lowers into the effect IR, a
combined approval surface, and a shared email-exfiltration benchmark — are
collected in [Guardians synergies](../todos/guardians-synergies.md).

## Dogwood

AWS's [Dogwood](https://aws.amazon.com/blogs/opensource/introducing-dogwood-runtime-verification-for-ai-agents/)
(2026, Apache 2.0, reference implementation at
[`dogwood-policy/dogwood`](https://github.com/dogwood-policy/dogwood)) is a
policy language for agent tool calls that extends
[Cedar](cedar.md) with *temporal* conditions drawn from
Metric First-Order Temporal Logic (MFOTL), the specification language of the
runtime-verification literature. Every syntactically valid Cedar policy is a
valid Dogwood policy; the extension is a `when temporal { ... }` clause that may
look back over the agent's recent event history.

Each tool interaction yields two events — `Action::"Name"::request` and
`Action::"Name"::response` — carrying inputs, outputs, and a timestamp. Policies
match them by pattern, with `_` wildcards, and a macro library over the MFOTL
primitives (`formerly`, `previous`, `since`) supplies the operators an author
actually writes:

```
permit ( principal, action == AgentCore::Action::"SellShares", resource )
when temporal {
    formerly within 1h AgentCore::Action::"ApproveSale"::response{
        input.stock:     context.input.stock,
        input.shares:    context.input.shares,
        output.approved: true
    }
};
```

— "sell only if a matching approval came back within the hour" — alongside
`count_within`, `count_distinct_within`, and `sum_within` for windowed rate,
distinct-value, and numeric limits, and `bind` for naming an aggregate so one
window can be compared against another. Policies lower to ordinary Cedar with
the temporal results filled into context slots at decision time. Dogwood ships
in Amazon Bedrock AgentCore's policy layer, which generates the action schema
from an agent's MCP tool manifest.

The aggregate vocabulary is nearly the same as this project's. `clauz3`'s
relation language has `count`, `distinct`, and `sum` filtered by `where`, and
both systems derive their schema from tool signatures rather than hand-written
declarations — Dogwood from an MCP manifest, `clauz3` from trusted function
signatures. What differs is the domain of quantification:

| | `clauz3` | Dogwood |
| --- | --- | --- |
| Quantifies over | every trusted call on every branch of one program | every tool event in a time window, across the session |
| Decided | before any effect runs | at each tool call, against history |
| Rejection unit | the whole program | the individual call, mid-run |

So `bank.max_spend(500)` and `sum_within(a, 1h, ...) > 5000` look alike and are
not comparable claims. The first says "this program cannot spend more than $500
whichever branch it takes," established with nothing yet having happened; the
second says "the agent has not already spent $5000 this hour," decided with the
earlier spends already irreversible. Neither subsumes the other. A static proof
cannot bound what a *second* invocation does an hour later; a windowed monitor
cannot tell a user, before they consent, what the program in front of them will
do.

Two things Dogwood addresses that static proof structurally cannot:

- **Cross-invocation budgets.** Every guarantee `clauz3` discharges is scoped to
  one program. Five separately-proved programs, each provably spending at most
  $500, spend $2500 between them, and no per-program proof has anything to say
  about the fifth one.
- **Concurrency.** The blog is explicit that a transfer limit must match
  `::request` events and not only `::response`, because an agent that submits in
  parallel evades a cap computed over settled transfers alone. `clauz3`'s
  all-or-nothing execution removes *intra*-program partial effects, but two
  proved programs running concurrently is the same hole, and the proof layer
  cannot see it.

And one assumption it shares with everything else here: soundness rests on the
event history being true. Dogwood's README is candid that the reference
interpreter does not authenticate events, guarantee timestamp integrity, or make
traces durable. That is the same assume/guarantee boundary `clauz3` calls the
[environment contract](#environment-contract) and FORGE calls its observability
service — in each case the formal layer is only as good as what the environment
promises to publish.

Time is also the axis where `clauz3` is thinnest. Effect facts are an unordered
multiset carrying path conditions; as the Guardians discussion notes, there is
no effect *order* on which an automaton could run.
[Fluents](../reference/fluents.md) recover part of it — successor-state axioms
give the post-state, so "every door is locked at the end" becomes expressible —
but that is a terminal-state property of a single program, not a metric window
over a session. Dogwood is the sharpest available statement of what the missing
dimension looks like taken seriously, and its roadmap item for liveness ("what
must happen") is the runtime counterpart of what `Fluent.final` checks
statically.

One of Dogwood's scenarios is ported into a worked example under
[`examples/trading`](../examples/trading.md): the approval gate above, with its
first three clauses rendered as provenance contracts and `within 1h` left
unrepresented. The walk-through is also where the cost side of the comparison is
argued — what a monitor must be able to trust about its event history once the
system has more than one moving piece.

Where the two compose is the approval service rather than the prover.
[Approval policies](../todos/approval-policies.md) already put a policy admin
between the agent and the user, deciding by *entailment* — conjoining a rule's
contracts onto the program and re-proving. That layer is point-in-time today,
exactly as Cedar is: it sees one request in isolation. A Dogwood-shaped
extension would give it the history it lacks — "auto-approve only if a matching
human approval was granted within the hour," "reject if the programs approved
today have already committed $5000."

That composition is stronger than either half, because a *proved* contract is a
better thing to meter than a raw tool call. `bank.max_spend(500)` is a sound
upper bound on the entire program, so a monitor can debit $500 from the hourly
budget at approval time and never be surprised by what the program then does.
Debiting at consent rather than at settlement is also what closes the
concurrency gap above, since the budget moves before anything runs. The cost is
conservatism — a program that pays `min(balance, $500)` and actually spends $12
has been charged the full bound — which argues for reconciling against the
receipt once the run completes, and makes the receipt track
([issue #47](https://github.com/cmungall/agent-deal/issues/47) and the runtime
enforcement limitation in the README) load-bearing for more than defense in
depth.

## Agentic Resource Discovery (ARD)

Google's
[Agentic Resource Discovery specification](https://developers.googleblog.com/en/announcing-the-agentic-resource-discovery-specification/)
(2026) is related work of a different *kind* from the systems above. PCC, FORGE,
Guardians, and Dogwood all decide whether a particular behaviour is allowed. ARD
instead
answers three questions *about the counterparty*: where does a capability live,
which one should I use, and is the publisher who they claim to be. It is a
discovery and publisher-identity layer, not a behavioural one.

The mechanism is two-tier:

- **Catalogs** — an organization publishes a manifest of entries at
  `https://{domain}/.well-known/ard.json`, listing MCP servers, A2A agents,
  OpenAPI tools, skills, or nested catalogs, each typed by an open IANA media
  type. (The launch announcement called this file `ai-catalog.json`; the
  current draft treats that path as a predecessor that consumers may, but need
  not, consult.) Ownership of the domain is the cryptographic root of identity:
  an entry's `urn:air:<publisher>:...` identifier must be backed by a trust
  manifest whose identity belongs to that publisher domain.
- **Registries** — crawlers index catalogs so an agent can search in natural
  language or fetch a known partner's catalog directly, receiving the metadata
  needed to verify the publisher before connecting over the native protocol.

The trust ARD establishes is *provider authentication*: this server is operated
by the domain it claims. That is orthogonal to what `clauz3` proves. A
domain-verified MCP server can still drain an account or email the wrong people,
and a verified-then-compromised server is exactly the case a publisher-identity
check cannot catch. ARD's "is it safe to connect?" stops at the publisher;
`clauz3`'s question — what will *this* invocation provably do — begins after the
connection is made. The layering is clean:

| Layer | Question it answers |
| --- | --- |
| ARD | Where is the capability, and is the publisher authentic? |
| MCP / A2A | How do I invoke it? |
| `clauz3` | What will this invocation provably do? |

The concrete overlap is on the distribution side, not the proving side. The
[distributed trusted layers](distributed-trusted-layers.md) model already ships a
trusted layer as a small, testable package, and
[issue #47](https://github.com/cmungall/agent-deal/issues/47) tracks pinning and
attesting those layers across repositories. ARD's domain-rooted identity is a
candidate *origin* anchor for that work, and a possible interop target if trusted
layers are ever published for discovery. The distinction to keep sharp is that
ARD binds a capability to a domain (origin), while #47's signing track needs
content attestation — that the installed bytes match what was attested. ARD
supplies the former; it does not supply the latter, and the GitHub Actions
attestation path in #47 is what closes the content gap. The two compose: ARD to
find and authenticate the publisher, `clauz3` to constrain what the thing is
allowed to do once connected.

Two details of the [v0.91 specification](https://agenticresourcediscovery.org/spec/)
(as checked in October 2026) bear on that composition. First, `trustManifest`
is an open envelope: ARD requires its `identity` binding but delegates the
schema and verification of attestations, provenance, and signatures to the
trust framework declared in `trustSchema`. A trusted-layer entry could point
to a GitHub Actions attestation for a commit; a verifier must still validate
that evidence under the declared framework. Second, ARD binds the publisher's
DNS domain to that identity, whereas `clauz3 install gh:org/repo@<ref>` resolves
a GitHub repository and commit. An origin cross-check would bind the domain's
catalog to the GitHub origin in addition to verifying the installed content.
Whether to build that integration is tracked in
[issue #86](https://github.com/cmungall/agent-deal/issues/86).

## Neuro-Formal Verification (NFV)

Lahiri, [Neuro-Formal Verification: Agentic Language-Agnostic Formal Program
Reasoning](https://arxiv.org/html/2608.21516v3) (Microsoft Research, 2026),
introduces **NFV**. The link is pinned to arXiv **v3** (September 2026); every
number below is from that revision.

NFV is not an agent-safety system. It asks the classical verification question —
does this program satisfy this property in this environment — for code written
in a mainstream language, and answers it by having an agent translate the
problem into a verification-aware language and a sound verifier discharge it.
It belongs here because it is the other obvious way to put an agent and a
verifier in the same loop, and because it makes the opposite choice from
`clauz3` at almost every step.

The pipeline has five stages, parameterized by a target language *F* (Dafny in
the proof-based arm; CBMC in a bounded, refutation-only arm):

1. **intent formalization**: from documentation, synthesize the environment
   theory *E_F* — library contracts (1a) and the program's preconditions (1b);
2. **program translation** from Python into *F*, done neurally;
3. **specification translation** of the source-level assertion into *F*;
4. **agentic proof search**: the agent proposes invariants and lemmas, the
   verifier checks each one;
5. **proof mapping** back to a sketch over the source (described, not yet
   implemented).

What keeps the agent honest is a **goal-blind staged discipline**. The
environment theory and precondition are frozen before the goal is visible, so
neither can be chosen to suit it. Every translated line carries a provenance
tag — `src:` quoting its Python line verbatim, or `synth:` for code with no
source counterpart — and untagged code is rejected. The translation repair loop
sees only syntax and resolution errors, never a verifier goal. Proof search may
add lemmas and proof calls but may not touch any `src:` line, the frozen theory,
the precondition, or the goal, and those are re-checked mechanically on every
round.

Refutation is treated as a proof too: "a proof failure cannot be interpreted as
a bug — an unproved goal is evidence about the proof search, not about the
program." To refute, the goal is negated mechanically, the agent proposes a
witness input from the Python source alone, the witness is installed as a
precondition, and the verifier must *prove* the negated goal. Otherwise the
pipeline abstains.

On 206 Python programs (103 correct, 103 buggy mutants, from `nl2postcond`), the
Dafny pipeline resolves 57% of entries at 92% precision. The two baselines are
the interesting part. An LLM-as-judge answers every entry at 72% precision with
no checkable artifact. An **unstaged** agent–verifier combination proves 98% of
the correct programs and 98% of the buggy ones — 50% precision, a coin flip. A
verifier in the loop is worth nothing if the agent is free to reshape what it
is verifying. And among the staged pipeline's wrong verdicts, the paper
attributes most to one cause: **incorrect precondition synthesis in *E_F***.

### How it lines up

|  | NFV | `clauz3` |
| --- | --- | --- |
| Question | does *P* meet a functional property? | what effects can *P* have? |
| Property from | the developer (docstring, assertion) | the agent proposes; the user consents |
| Program model | neural translation into Dafny or C | the Python subset itself, symbolically executed |
| Environment model | synthesized per problem from documentation, then frozen | a hand-written, audited trusted layer, fixed across programs |
| Unsupported code | translated anyway, best effort | refused |
| Soundness posture | empirical accuracy; every verdict machine-checked over a model | sound for the subset, relative to the trusted layer |
| Negative verdict | a proof of the negated goal on a witness, else abstain | no proof, so the program does not run |

The deepest difference is where the translation gap sits. NFV's model of the
program is produced by an LLM, so "the proof is about *this* program" is itself
an empirical claim; the provenance tags exist to make that claim auditable.
`clauz3` closes the gap by construction instead — no translation, a restricted
subset, and `clauz3.interp` running the same subset the prover modelled (see
[subset-interpreter](../todos/subset-interpreter.md)) — and pays for it by
refusing anything it cannot model. That trade is only affordable because the
programs are short, agent-written, and disposable; NFV targets code that
already exists.

### What it confirms

Both of NFV's negative results bear on choices `clauz3` has already made:

- **The agent must not shape the environment.** NFV's dominant failure mode is
  the agent synthesizing a wrong environment theory, even under goal-blind
  staging. `clauz3` never lets the agent author the environment: the
  [environment contract](#environment-contract) is a small, human-audited
  trusted layer, and the agent's code can only import from it. NFV is a
  measurement of what the alternative costs.
- **The unstaged baseline is contract weakening, measured.** An agent allowed
  to reshape the verification problem proves nearly everything. `clauz3`'s
  version of that risk is an agent proposing a contract weak enough to always
  discharge. The answer here is visibility rather than freezing — the contract
  is the thing the user reads, and `no_guarantees()` is explicit rather than
  silent — but the experiment is a good argument for never letting a weak
  contract look like a strong one.

The refutation discipline also sharpens a distinction the prover already draws.
Its outcomes are not one bit: `failed` carries a Z3 model — a concrete
counterexample, and so a genuine refutation relative to the trusted layer —
while `skipped` (an unsupported construct, an encoding the solver rejects, an
undecided check) is only evidence about the prover, in NFV's sense. Both stop
the run, but only the first says the program is wrong, and approval UIs and
agent feedback should keep them apart. `vacuous`, raised when the program's
assumptions are provably unsatisfiable, is the counterpart of NFV's check that
a synthesized precondition is not inconsistent.

### Where they compose

The seam is the trusted layer. The [environment contract](#environment-contract)
section above ends on "if a trusted stub lies, the proof result is only as good
as that lie", and today the only defence is keeping stubs small enough to audit
by eye. That is exactly the problem NFV's stage 1 addresses: formalizing a
library's contract from its documentation, attested against tests. Two
applications fit:

- **Drafting trusted-layer specs.** A `@clauz3.requires`/`@clauz3.ensures`
  proposal generated from an API's documentation, attested against its tests,
  then reviewed by a human before it enters a trusted root. NFV's finding that
  this is where agents go wrong most is a reason to keep the human review, not
  to skip the drafting.
- **Checking a stub against its implementation.** A trusted effect's declared
  contract is a property of its Python body — the kind of question NFV answers.
  Running NFV over trusted-layer implementations would give each stub a
  machine-checked (if model-relative) argument that it does what it declares,
  where today there is only review and the runtime `requires`/`ensures` checks.

NFV is not a fallback for agent programs outside the proved subset: an
empirically accurate verdict is the wrong basis for consent, which needs the
guarantee to hold on the branch that actually runs. Its value here is one layer
down, raising confidence in the assumptions every `clauz3` proof already rests
on.

## Operator Policy Versus Agent-Abduced Contracts

FORGE, Guardians, and Dogwood all assume policies are written by humans up front
and applied to agents — Dogwood most explicitly of the three, since it inherits
Cedar's operator-authored `permit`/`forbid` framing wholesale. That is the right
shape for organizational rules the agent should not be able to weaken.

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
- their `@clauz3.requires(...)` decorators define required preconditions
- their `@clauz3.trusted` decorators identify trusted effect boundaries
- their `@contract` helpers define trusted domain contract vocabulary

If a trusted stub lies, the proof result is only as good as that lie. Keeping
this layer small and auditable is central to the design.

## Mechanics Worth Borrowing

FORGE gives names and mechanisms to several ideas that fit this project.

Environment contract:

The phrase is better than just "trusted stubs." It makes the assume/guarantee
boundary explicit: the prover records calls to `@clauz3.trusted` functions and
assumes their declared postconditions; the environment must make those
postconditions true.

Datalog frontend:

The current relation API is already closer to a declarative policy language
than the old solver-callback API:

```python
from clauz3.spec import ContractSpec, contract, effect
from tools.email.trusted.effects import send_email

Email = effect(send_email)


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
unlock that class of policy. Guardians' source-sink taint model is the sharpest
static articulation of this idea, and [CaMeL](#camel)'s per-value capabilities
— a source tag plus a reader set, propagated by the interpreter — the sharpest
dynamic one; see
[Guardians synergies](../todos/guardians-synergies.md) for how it could attach
to the `FactInfo` layer.

Benchmarks:

The repo has examples by proof shape. It does not yet have an adversarial
benchmark. Even a small corpus of plausible agent-authored programs that try to
violate contracts would make prover coverage more measurable. The Guardians
email-exfiltration scenario is a natural first adversarial case; see
[Guardians synergies](../todos/guardians-synergies.md#track-5-shared-examples-benchmark).

## What Is Distinct Here

Two properties remain specific to this design:

1. Static proof gives a binary go/no-go on the entire program before any trusted
   effect happens.
2. Contract abduction makes the agent propose the permission artifact, while the
   prover and user decide whether it is acceptable.

Those are the pieces worth preserving even if future versions borrow a Datalog
frontend, provenance facts, or runtime enforcement from related systems.
