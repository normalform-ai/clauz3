# Risk Taxonomies and Where ClauZ3 Fits

This page surveys the main published taxonomies of AI and agent risk and
builds a small formal framework for answering one question about each risk
category: *does a discharged `clauz3` contract bound it, and under which
assumptions?*

The short answer: `clauz3` is a **static, all-paths enforcement mechanism for a
specific class of safety properties over a program's trusted effects**. It
fully addresses the "provided-and-unsafe action" core of agent risk (OWASP's
*Excessive Agency*, Google's *Rogue Actions*, IMDA's *Unauthorised Actions*),
regardless of whether the cause is prompt injection, misalignment, or plain
error. It does so by replacing a question about the model ("was the agent
fooled?") with five questions about deterministic components. It does not
address content harms, confidentiality as a hyperproperty, ordering, or
cross-run budgets, and it introduces risks of its own at the consent and
trusted-layer boundaries.

For the shorter comparison with sandboxes, guardrails and monitors, see
[How ClauZ3 compares](comparison.md); for the design lineage, see
[Background](background.md).

## 1. The landscape

Published taxonomies fall into three families. They answer different
questions, so a mitigation has to be placed against all three.

| Family | Question it answers | Representative sources |
| --- | --- | --- |
| **Harm-domain** | *What bad outcome?* | MIT AI Risk Repository; NIST AI 600-1; Weidinger et al.; AIR 2024; International AI Safety Report; EU GPAI Code of Practice |
| **Threat / failure-mode** | *How does an agent system get there?* | OWASP LLM Top 10 and Agentic Top 10; OWASP ASI threats T1–T15; Microsoft failure-mode taxonomy; MITRE ATLAS; CSA MAESTRO; Google SAIF; IMDA agentic framework |
| **Enforcement / formal** | *Which mechanism can rule it out, and what must be trusted?* | Schneider's enforceable policies; Hamlen–Morrisett–Schneider computability classes; hyperproperties; STPA; Guaranteed Safe AI; prompt-injection design patterns |

### 1.1 Harm-domain taxonomies

- **MIT AI Risk Repository** (Slattery et al., arXiv:2408.12622; *Patterns*
  2026). It has two parts. The **causal taxonomy** codes each risk by *Entity*
  (Human / AI / Other), *Intent* (Intentional / Unintentional / Other) and
  *Timing* (Pre- / Post-deployment / Other). The **domain taxonomy** has 7
  domains and 24 subdomains; subdomain 7.6 *Multi-agent risks* was added in
  v2, April 2025. Agent side effects sit mainly in 7.3 (*Lack of capability or
  robustness*), 7.1 (*AI pursuing its own goals*), 2.1/2.2 (privacy and
  security) and 5.1 (*Overreliance and unsafe use*).
- **NIST AI 600-1** (Generative AI Profile, July 2024). It names 12 risks: CBRN
  Information or Capabilities; Confabulation; Dangerous, Violent, or Hateful
  Content; Data Privacy; Environmental Impacts; Harmful Bias or Homogenization;
  Human-AI Configuration; Information Integrity; Information Security;
  Intellectual Property; Obscene, Degrading, and/or Abusive Content; Value Chain
  and Component Integration. The profile is about content. Agents appear only as
  a security consideration.
- **Weidinger et al.** (arXiv:2112.04359; FAccT 2022) groups risks into six
  areas: discrimination/hate/exclusion; information hazards; misinformation;
  malicious uses; human–computer interaction harms; and environmental and
  socioeconomic harms. It predates agents and is about outputs, not effects.
- **AIR 2024** (Zeng et al., arXiv:2406.17864). It distils 8 government and 16
  corporate policies into 314 categories in four levels. Level 1: *System &
  Operational*, *Content Safety*, *Societal*, *Legal & Rights*. Only the first
  concerns actions.
- **Catastrophic and societal-scale framings.** Hendrycks et al.
  (arXiv:2306.12001) use four categories: malicious use, AI race,
  organizational risks, rogue AIs. Critch & Russell's TASRA (arXiv:2306.06924)
  uses an accountability tree; its *bigger-than-expected* and
  *worse-than-expected* impact types match unintended program side effects.
- **International AI Safety Report** (Bengio et al., 2025; 2nd ed. Feb 2026).
  It groups risks as *Malicious use*, *Malfunctions* (reliability, loss of
  control) and *Systemic risks*. The 2026 edition says agents "pose heightened
  risks because they act autonomously".
- **EU GPAI Code of Practice, Safety & Security chapter** (July 2025). It names
  four systemic risks: CBRN, loss of control, cyber offence, harmful
  manipulation. Its Appendix 1.3 is the most agentic of the regulatory texts:
  it lists tool and computer use, autonomy, the "level of human oversight" and
  "access to tools … or physical systems" as risk sources. It also lists
  "difficult or impossible to reverse" as an aggravating characteristic.
- **Frontier safety frameworks** (Anthropic RSP, OpenAI Preparedness, DeepMind
  FSF) track capability thresholds: CBRN, cyber, AI R&D/autonomy, misalignment
  and manipulation. They are not side-effect classes. `clauz3` belongs to their
  *deployment safeguard* sections, for example Anthropic's "misaligned AI
  systems in high-stakes settings" with access to sensitive assets.

**Observation.** None of the general taxonomies has a first-class category for
"an AI system's ordinary actions have unintended side effects". It is spread
across MIT 7.3 with causal code *(AI, Unintentional, Post-deployment)*, IASR
*Malfunctions*, and TASRA types 2 and 3. Agent-specific taxonomies fill that
gap.

### 1.2 Agent threat and failure-mode taxonomies

- **OWASP Top 10 for LLM Applications 2025.** LLM01 Prompt Injection … LLM10
  Unbounded Consumption. **LLM06 Excessive Agency** names three root causes:
  excessive *functionality*, excessive *permissions* and excessive *autonomy*.
  Its mitigations include "require user approval" for high-impact actions and
  "implement authorization in downstream systems rather than relying on an LLM
  to decide".
- **OWASP ASI, *Agentic AI – Threats and Mitigations* v1.0** (Feb 2025) lists
  fifteen threats:
  - T1 Memory Poisoning, T2 Tool Misuse, T3 Privilege Compromise
  - T4 Resource Overload, T5 Cascading Hallucination, T6 Intent Breaking &
    Goal Manipulation
  - T7 Misaligned & Deceptive Behaviors, T8 Repudiation & Untraceability,
    T9 Identity Spoofing
  - T10 Overwhelming Human-in-the-Loop, T11 Unexpected RCE and Code Attacks,
    T12 Agent Communication Poisoning
  - T13 Rogue Agents, T14 Human Attacks on Multi-Agent Systems, T15 Human
    Manipulation
- **OWASP Top 10 for Agentic Applications 2026** (Dec 2025) lists ASI01 Agent
  Goal Hijack, ASI02 Tool Misuse and Exploitation, ASI03 Identity and Privilege
  Abuse, ASI04 Agentic Supply Chain, ASI05 Unexpected Code Execution, ASI06
  Memory & Context Poisoning, ASI07 Insecure Inter-Agent Communication, ASI08
  Cascading Failures, ASI09 Human-Agent Trust Exploitation and ASI10 Rogue
  Agents. These names come from secondary summaries; the official PDF is gated,
  so check it before quoting.
- **Microsoft, *Taxonomy of Failure Modes in Agentic AI Systems*** (Apr 2025)
  crosses *security vs. safety* with *novel vs. existing*. It names
  human-in-the-loop bypass, incorrect permissions, excessive agency and loss of
  data provenance, among others. One of its mitigation areas is "control flow
  control": deterministic safeguards on the flow of execution.
- **MITRE ATLAS** includes agent techniques where the attacker's payload is a
  tool call: AML.T0053 *AI Agent Tool Invocation*, AML.T0086 *Exfiltration via
  AI Agent Tool Invocation* and AML.T0101 *Data Destruction via AI Agent Tool
  Invocation*.
- **Google** (Díaz, Kern & Olive, *Google's Approach for Secure AI Agents*,
  2025) names two risks, **rogue actions** and **sensitive data disclosure**,
  and three principles: well-defined human controllers, limited agent powers,
  observable actions and planning. **SAIF** lists "Rogue Actions" as one of 15
  risks.
- **Singapore IMDA, Model AI Governance Framework for Agentic AI** (v1.0 Jan
  2026; v1.5 May 2026) has five risk types: *erroneous actions*, *unauthorised
  actions*, *biased or unfair actions*, *data breaches*, and *disruption to
  connected systems*. It is the most action-centric taxonomy we found.
- **Governance framings.** Chan et al. (FAccT 2023) define agency by four
  characteristics: underspecification, directness of impact, goal-directedness
  and long-term planning. Shavit et al. (OpenAI, 2023) set out seven practices;
  §4.2 is *Constraining the Action-Space and Requiring Approval*, and it names
  the problems of approval context and approval fatigue. Kolt (*Governing AI
  Agents*, Notre Dame L. Rev. 2026) frames the problem as principal–agent.
  Kasirzadeh & Gabriel (arXiv:2504.21848) profile agents by autonomy, efficacy,
  goal complexity and generality. Feng, McDonald & Zhang (arXiv:2506.12469)
  give five autonomy levels named after the user's role, one of which is
  *approver*.
- **Security heuristics.** Willison's **lethal trifecta** (June 2025) is
  private data + untrusted content + external communication. Meta's **Agents
  Rule of Two** (Oct 2025) says an agent should hold at most two of [A]
  untrusted input, [B] sensitive access, [C] state change or external
  communication. An agent that holds all three needs supervision "or another
  reliable means of validation".
- **Benchmark-derived taxonomies.**
  - ToolEmu (ICLR 2024): privacy breach, data loss, financial loss, safety
    hazards, and others, all arising from *instruction underspecification*.
  - R-Judge (10 risk types).
  - Agent-SafetyBench (8 risk categories, 10 failure modes M1–M10, e.g. M4
    "ignoring known constraint information" and M6 "incorrect parameters").
  - InjecAgent: *direct harm* vs. *data stealing*.
  - AgentDojo: injection tasks with formal checks on environment state.
  - AgentHarm: malicious-*user* misuse, a different threat model.

### 1.3 Formal and enforcement frameworks

- **Schneider** (TISSEC 2000): an execution monitor that can only halt the
  system enforces exactly the **safety** properties.
- **Ligatti, Bauer & Walker**: edit automata, which can also suppress or insert
  actions, enforce more than that.
- **Hamlen, Morrisett & Schneider** (TOPLAS 2006) separate three enforcement
  classes: *static analysis* (decidable properties of program text),
  *execution monitoring* (coRE safety) and *program rewriting*.
- **Clarkson & Schneider** (JCS 2010): confidentiality and noninterference are
  **hyperproperties**, sets of sets of traces, and cannot be expressed as a
  property of single traces.
- **STPA** (Leveson) has four types of unsafe control action (UCA):
  1. not provided when needed;
  2. provided and hazardous;
  3. wrong timing or order;
  4. stopped too soon or applied too long.
- **Guaranteed Safe AI** (Dalrymple et al., arXiv:2405.06624) has three
  components: a *world model*, a *safety specification*, and a *verifier* that
  produces an auditable proof.
- **Prompt-injection design patterns** (Beurer-Kellner et al.,
  arXiv:2506.08837) list six: Action-Selector, Plan-Then-Execute, LLM
  Map-Reduce, Dual LLM, Code-Then-Execute and Context-Minimization.
- **Formal agent defences**, grouped by where they enforce:
  - *Information-flow interpreters and planners*: CaMeL (arXiv:2503.18813) and
    FIDES (arXiv:2505.23643).
  - *Runtime privilege and rule enforcement*: Progent (arXiv:2504.11703),
    AgentSpec (arXiv:2503.18666) and MiniScope (arXiv:2512.11147).
  - *Contextual policies*: Conseca (arXiv:2501.17070).
  - *LLM-based shields*: ShieldAgent and GuardAgent.
  - *Isolation*: IsolateGPT (NDSS 2025).
  - *Surveys*: the Dantas et al. survey (arXiv:2608.14590) finds that
    specification is the field's bottleneck and names "enforcing safety across
    whole plans" as a top open problem.

## 2. A formal model of a ClauZ3 run

The notation below is deliberately small. It is enough to say precisely what a
proof buys and where the remaining risk lives.

### 2.1 Ingredients

```text
Σ            effect alphabet: the trusted effects, each with typed fields
             (send_email(addr, msg), pay(account, amount), write_file(path, …))
Ω            environment inputs: every value a trusted call can return
             (tool results, DB rows, fetched pages — including adversarial ones)
P            the agent's program, in the supported Python subset
⟦P⟧(ω)       the effect traces τ ∈ Σ* that P can produce on inputs ω ∈ Ω
A            assumptions: trusted pre/postconditions and successor-state axioms
α(τ)         the abstraction the prover reasons over:
             α(τ) = (bag(τ), final(τ))
             bag(τ)   — the multiset of effect facts (order discarded)
             final(τ) — the end-state valuation of declared fluents
V            the contract vocabulary: @contract helpers authored by the TLE
C            a contract: a conjunction of instances of V, i.e. a predicate on α(τ)
I            the user's (unwritten) intent: the set of traces they would accept
```

### 2.2 The judgement

The prover establishes

```text
P ⊢_A C   ⟺   ∀ω ∈ Ω.  ∀τ ∈ ⟦P⟧(ω).  A(τ) ⇒ C(α(τ))
```

The user then approves `C`. `clauz3 run` executes `P` only after a successful
proof and a signed approval receipt.

Two features of this judgement drive most of the mapping below:

1. **The quantifier over ω.** Tool results are symbolic, so the proof holds for
   *every* value the environment could return, adversarial values included.
2. **The abstraction α.** It decides which properties `C` can express at all.

### 2.3 Proposition 1: effect bounds are injection-invariant

> Let `x` be any untrusted content that reaches `P` *at run time* through a
> trusted call (an email body, an issue comment, a web page). If `P ⊢_A C`,
> then whatever `x` is, the executed trace satisfies `C`.

This is immediate from the quantifier over ω. It is the formal content of the
claim that "the bound survives a manipulated agent". Injected data can steer
*which* branch runs. It cannot steer the program outside `C`.

Injection that happens *before* `P` is written is a different case: the agent
read something hostile and then wrote the program. There the proposition says
nothing about `P`. The defence moves to the user, who sees `C` and not the
agent's prose.

### 2.4 Theorem 2: soundness, relative to six assumptions

Let `τ*` be the trace that actually executes. Then `τ*` is acceptable
(`τ* ∈ I`) provided all of the following hold:

| # | Assumption | Fails when… | Who carries it |
| --- | --- | --- | --- |
| S1 | **Prover soundness.** `P ⊢_A C` implies the semantic statement. | encoder/solver bug, unsupported construct not refused | `clauz3` core |
| S2 | **Environment fidelity.** Real effects satisfy `A` and are faithfully reflected as facts. | a trusted stub under-declares, lies, or quietly no-ops ([vacuous proofs](../how-to/vacuous-proofs.md)) | TLE |
| S3 | **Vocabulary integrity.** Each helper in `V` returns the spec its docstring claims. | helper body branches on the environment ([helper bodies](concepts.md#why-helper-bodies-must-stay-boring)) | TLE |
| S4 | **Complete mediation.** Every consequential effect of `P` is in `Σ`. | an import escape, an unwrapped side channel, unrestricted harness tools | import allowlist + sandbox + harness |
| S5 | **Execution integrity.** The program that runs is the program proved and approved. | receipt not bound into the trusted boundary; tampering between prove and run | approval service + runner |
| S6 | **Consent adequacy.** `C ⊆ I`: every trace satisfying `C` is one the user accepts. | user approves a weak contract; approval fatigue; `C` misread | user, approval UI, approval policies |

S1–S5 make `P`'s behaviour satisfy `C`; S6 makes `C` acceptable. The proof is
just composition.

**Corollary 3 (risk transfer).** The probability that a harmful effect
executes is bounded by the sum of the failure probabilities of S1–S6:

```text
Pr[ τ* ∉ I ]  ≤  Σᵢ Pr[ Sᵢ fails ]
```

The agent's alignment does not appear on the right-hand side, and neither does
its robustness to injection or its reasoning. An unprotected agent is bounded
by roughly `Pr[agent is fooled or wrong] × Pr[harm | fooled]`, a quantity
nobody can certify. `clauz3` replaces it with terms about small,
deterministic, auditable components and one human judgement (S6) about a short
statement. This is the core of where `clauz3` sits: it is a **risk-transfer
mechanism** from the model to the trusted computing base and the consent
surface.

Two qualifications keep the corollary honest:

- **S6 is not free.** The Dantas et al. survey names specification as the main
  bottleneck in agent verification. In `clauz3` that bottleneck becomes "is
  this guarantee strong enough?". Visible weakness (`no_guarantees()`),
  entailment-based [approval policies](../todos/approval-policies.md) and
  [coverage policies](../reference/coverage-policies.md) all exist to shrink
  the S6 term.
- **The bound only covers what α can see.** A harm that is not a function of
  `α(τ)` is outside the theorem entirely. Section 2.5 classifies those harms.

### 2.5 The property lattice: what α can express

Write `Φ` for the predicates over traces. The abstraction partitions harms by
the property class needed to rule them out:

```text
                    Φ_hyper   (relations between runs: noninterference, CI)   ✗
                       │
       Φ_cross-run   (budgets across programs / sessions / time)               ✗
                       │
       Φ_order       (A before B, time windows, rate limits)                   ✗
                       │
  ┌──────────────── Φ_trace (single-run trace properties) ────────────────┐
  │   Φ_bag   permutation-invariant: membership (all), emptiness (empty),  │ ✓
  │           uniqueness (distinct), cardinality/budget (count, sum),      │
  │           filtered aggregates (where), joins (shares_value),           │
  │           column provenance (value is field f of a returned row)       │
  │   Φ_end   end-state over fluents (Fluent.final …)                      │ ✓
  └────────────────────────────────────────────────────────────────────────┘
       Φ_content (semantic properties of field values: truthful, non-toxic,
                  persuasive, contains PII)                                   ✗ / partial
       Φ_live   (the program actually achieves the task)                       partial
```

- **Φ_bag ∪ Φ_end is what `clauz3` enforces today.** Membership, emptiness,
  uniqueness, cardinality, numeric budgets and filtered aggregates are
  *permutation-invariant*: they hold or fail regardless of effect order. Most
  are safety properties. Because programs terminate, existential forms ("at
  least one email to Y", `shares_value`) become decidable *bounded* checks.
  Fluents add order-sensitive *end states* without exposing order itself.
  Column-provenance contracts, as in the
  [malicious repo defence](../how-to/malware-repo-defense.md), are a
  restricted, *static* form of data provenance: "every URL passed to
  `download_zip` is the `asset_url` of a returned row".
- **Φ_order** (STPA UCA type 3) is excluded by design: the multiset discards
  order.
- **Φ_cross-run** is excluded: each proof is about one program, so five
  programs that each spend at most $500 spend $2,500 together.
- **Φ_hyper** is excluded *in principle*, not just in the current
  implementation. "The secret does not influence what Bob receives" compares
  two runs, and no all-paths check of single runs can express it (Clarkson &
  Schneider). `clauz3` can constrain the *channel* of a disclosure (to whom,
  through which effect). It cannot constrain the *information* the channel
  carries.
- **Φ_content** is mostly out of reach. A relation lambda can test syntactic
  properties of a field ("`@` in addr", a path prefix, equality to a literal).
  It cannot test whether an email body is true, kind or free of personal data.
- **Φ_live** is not guaranteed. A proof shows the program *may not* do bad
  things, not that it *will* do the good thing. Existential contracts and
  fluents recover some of this (ending with the door locked, a file existing).

### 2.6 Placement in enforcement theory

| Dimension | Position of `clauz3` |
| --- | --- |
| Hamlen et al. enforcement class | **Static analysis**, sound and incomplete, over a deliberately restricted program class. It fails closed: unsupported constructs refuse the proof. Backed by an **execution monitor** (runtime `requires`/`ensures` at the trusted boundary; Schneider). |
| Who writes the policy | The **agent proposes** the policy from a trusted vocabulary (contract abduction); the user approves it. Contrast operator-written policies in Progent, AgentSpec, Cedar, Dogwood and FIDES. |
| Timing | **Pre-execution, whole-program.** A monitor can block the second payment but not un-send the first. A `clauz3` proof rejects before the first effect. |
| Atomicity | **All-or-nothing authorisation, not a transaction.** Effects are not buffered: if a trusted call raises midway, earlier effects have happened. In edit-automaton terms, `clauz3` suppresses whole programs at authorisation time; it does not roll back at run time. |
| PCC analogy | Untrusted producer = agent; policy = contract; checker = prover. Unlike PCC, the consumer re-proves rather than checking a carried proof, so the TCB includes Z3 and the encoder. |
| Guaranteed Safe AI | A small, discrete, non-probabilistic instance: world model = trusted effect specs and fluents; safety spec = approved contract; verifier = Z3. GS AI's warnings carry over: residual risk concentrates in **spec adequacy (S6)** and **world-model fidelity (S2)**. |
| Design-pattern catalogue | **Code-Then-Verify-Approve-Execute**, a refinement of Code-Then-Execute. Unlike Plan-Then-Execute, data-dependent branching is allowed, because the proof covers every branch. |
| Autonomy levels (Feng et al.) | *Approver*, with approval raised from individual actions to a **specification** that covers every path. |
| Saltzer & Schroeder | Least privilege, *per program*: the contract is an authority envelope for one task. Fail-safe defaults: refuse what cannot be proved. Psychological acceptability: approve sentences, not code. Complete mediation is *assumed* (S4) and must come from the allowlist and sandbox. |

## 3. A coverage function for any taxonomy

To place `clauz3` against any risk category `r`, ask three questions:

1. **Channel.** Is the harm realised through an *effect* (something the
   program does in the world), through *content* (what the model says),
   through *disclosure* (information leaving its context), or is it *systemic*
   (many actors, markets, society)?
2. **Property class.** If the channel is an effect, which part of the lattice
   in §2.5 does ruling the harm out need?
3. **Cause.** Use the MIT causal codes (*Entity*, *Intent*, *Timing*) plus the
   point in the pipeline where the risk enters: model, context or input,
   program, effect, environment, human oversight, or multi-agent.

Then assign one verdict:

| Verdict | Meaning |
| --- | --- |
| **B — Bounded** | Effect channel, property in Φ_bag ∪ Φ_end. A discharged contract rules the harm out on every branch, for any cause, subject only to S1–S6. |
| **C — Channel-bounded** | Disclosure through an effect. `clauz3` bounds *who and which channel*, not *what information*. Full coverage needs IFC. |
| **V — Visible** | Not prevented, but surfaced in the approval artifact: a weak or null contract, a coverage warning, or a policy rejection. |
| **R — Relocated** | The risk moves onto an S-assumption. It does not disappear; it becomes a property of the TLE, sandbox, approval service or user. |
| **O — Out of scope** | Content, hyperproperty, ordering, cross-run, model-capability or societal harm. Needs another layer. |

The central point is that **the cause does not affect the verdict for B**.
Injection, misalignment, hallucination, misreading the instructions and a
malicious tool return all reach the world through `P`'s effects, and
Theorem 2 bounds those effects without reference to why `P` was written.
Verdicts B and C are *consequence-bounding*, not *cause-preventing*.

## 4. Mapping the major taxonomies

### 4.1 OWASP Top 10 for LLM Applications 2025

| ID | Risk | Verdict | Notes |
| --- | --- | --- | --- |
| LLM01 | Prompt Injection | **B** for effects; **V** for authorship | Proposition 1: run-time injection cannot push `P` outside `C`. Pre-authoring injection reaches the user only as a visible contract. |
| LLM02 | Sensitive Information Disclosure | **C** | Recipient, path and channel allowlists; no value-level confidentiality. |
| LLM03 | Supply Chain | **R** | Trusted layers are the new supply chain (S2/S3); see [publish](../how-to/publish-layers.md) and [install](../how-to/install-layers.md) layers. |
| LLM04 | Data and Model Poisoning | **B** for consequences | Same argument as LLM01: a poisoned model still writes a program that must prove `C`. |
| LLM05 | Improper Output Handling | **B** | Output is not executed by the host. It runs as a program in a subset with an import allowlist, and every effect goes through a trusted function with preconditions. |
| LLM06 | Excessive Agency | **B** | The direct target. *Functionality*: import allowlist, installed layers. *Permissions*: per-program contract instead of a tool grant. *Autonomy*: proof plus approval before any effect. |
| LLM07 | System Prompt Leakage | O | Content. |
| LLM08 | Vector and Embedding Weaknesses | **B** for consequences | Retrieval results are symbolic inputs ω. |
| LLM09 | Misinformation | O | Content; an effect that *publishes* it is C/B on channel only. |
| LLM10 | Unbounded Consumption | **B** if modelled | When cost is an effect field: `sum(cost) ≤ k`, `count() ≤ n`. Token use by the agent itself is out of scope. |

### 4.2 OWASP Agentic AI threats (T1–T15) and Agentic Top 10 (ASI01–ASI10)

| Threat | Verdict | Notes |
| --- | --- | --- |
| T1 / ASI06 Memory & context poisoning | **B** for consequences | Poisoned memory is another ω or another influence on authorship. |
| T2 / ASI02 Tool misuse | **B** | "Abuse … within authorized permissions" is exactly the gap between a tool grant and a contract. |
| T3 / ASI03 Privilege compromise, identity abuse | **B** within Σ; **R** outside | The contract bounds arguments, e.g. which account; credential handling is the TLE's (S2). |
| T4 Resource overload | partial **B** | Count and sum bounds on effects; not DoS against the agent host. |
| T5 / ASI08 Cascading hallucination, cascading failures | **B** within a run; O across runs | Cross-run cascades need Φ_cross-run. |
| T6 / ASI01 Goal manipulation, goal hijack | **B** + **V** | A hijacked goal yields either a provable program inside `C` or a different, visible `C`. |
| T7 / ASI10 Misaligned, deceptive, rogue agents | **B** | The agent is untrusted by construction. Deception about *what the code does* is moot because the user approves the proof. |
| T8 Repudiation & untraceability | partial | Signed approval receipts attest consent to `C` for `P`; durable audit logging is the service's job. |
| T9 Identity spoofing | O / **R** | Authentication of principals lives outside `clauz3`. |
| T10 Overwhelming human-in-the-loop | **mitigated, and a risk to S6** | One approval per program instead of per call; entailment-based auto-decisions for routine cases. Fatigue at the contract level is still possible. |
| T11 / ASI05 Unexpected RCE and code attacks | **B** | The agent's code is *meant* to be executed, so the subset plus the import allowlist (S4) is the defence. Must still run inside a sandbox. |
| T12 / ASI07 Agent communication poisoning | **B** for consequences | Messages are inputs. |
| T13 Rogue agents in multi-agent systems | partial | Each agent's programs are bounded; collusion across programs is Φ_cross-run. |
| T14 Human attacks on multi-agent systems | O | |
| T15 / ASI09 Human manipulation, trust exploitation | **V** | The agent can persuade the user to approve a weak contract. The approval card demotes agent prose and shows what is proved (S6). |
| ASI04 Agentic supply chain | **R** | As LLM03. |

### 4.3 Action-centric taxonomies

**IMDA (2026) five risk types:**

| Risk type | Verdict | Notes |
| --- | --- | --- |
| Erroneous actions | **B** for anything the user's contract excludes | A correct-by-contract action that is still the wrong one is S6 or Φ_live. |
| Unauthorised actions | **B** | This is the defining case. |
| Biased or unfair actions | O | Unless the bias is expressible over effect fields, e.g. a per-recipient count. |
| Data breaches | **C** for exposure; **B** for manipulation | Manipulation is a write effect, so it is bounded. |
| Disruption to connected systems | **B** where modelled | Count, rate and target bounds; not ordering. |

**Google.** *Rogue actions* is **B**. *Sensitive data disclosure* is **C**. Of
the three principles, `clauz3` mechanises the second, "agent powers must have
limitations", per program. It makes plans observable at the level of proved
guarantees, which serves the third.

**Microsoft (2025).** *Excessive agency*, *incorrect permissions*, *agent flow
manipulation* and *function compromise* are **B**; function compromise turns
into S2 when the compromised function is trusted. *Human-in-the-loop bypass* is
handled structurally, because the agent has no path to an effect except
through `clauz3 run`; this relies on S4 and S5. *Loss of data provenance* is
partially covered by column-provenance contracts. *Insufficient intelligibility
for meaningful consent* is the problem `clauz3`'s approval artifact is
designed to address.

**ATLAS.** AML.T0053 (*tool invocation*) and AML.T0101 (*data destruction via
tool invocation*) are **B**. AML.T0086 (*exfiltration via tool invocation*) is
**C**.

### 4.4 Lethal trifecta and the Rule of Two

`clauz3` removes none of the three legs. What it does is replace the third leg,
"can change state or communicate externally", with "can change state or
communicate *only within a proved envelope C*":

- **Exfiltration to an attacker-chosen destination** is ruled out when `C`
  pins destinations, e.g. `only([...])`, a path prefix, or a URL taken from a
  trusted column.
- **Exfiltration through an allowed destination** remains possible, for
  example private data placed in the body of an email to an approved recipient.
  That is a hyperproperty, so it is **C**, not **B**.
- **State-changing harm** (the Rule of Two's broader [C]) is **B** for
  everything `C` excludes.

So, for the Rule of Two, `clauz3` is a candidate for "another reliable means of
validation" in the A+B+C configuration. The stated caveat is that the
confidentiality part of [B]+[C] needs IFC.

### 4.5 STPA unsafe control actions

| UCA type | Verdict | Mechanism |
| --- | --- | --- |
| 1. Not provided when needed | partial | Existential contracts; fluent end states ("door locked at end"). |
| 2. Provided and hazardous | **B** | `all`, `empty`, `where`, `distinct`. |
| 3. Wrong timing or order | O | The multiset discards order; fluents capture only end state. |
| 4. Stopped too soon or applied too long | partial | `count`/`sum` capture *how much*; nothing captures *how long*. |

STPA's "flawed process model" in the controller is a good description of an
LLM that misreads its task. `clauz3` does not repair the controller's model.
It moves the correctness obligation onto a checked specification instead.

### 4.6 Benchmark failure modes

- **Agent-SafetyBench.**
  - **B**: M4 (*ignoring known constraint information*), M6 (*incorrect
    parameters*), M7 (*ignoring a tool's safety issues*), M9 (*over-trusting
    tool results*) and M10 (*failing to filter tool results*). Each is a cause
    whose consequence a contract bounds.
  - **Φ_live**: M8 (*failing to call necessary tools*).
  - **O**: M1 (*harmful content without tool calls*).
- **ToolEmu.** Financial loss, data loss, computer-security compromise and
  safety hazards are **B** when modelled. Privacy breach is **C**. Reputational
  and legal harms depend on content (O).
- **InjecAgent.** Direct harm is **B**; data stealing is **C**.
- **AgentHarm.** Here the *user* is malicious. `clauz3` protects the user from
  the agent, not the world from the user, so this is O by design.

### 4.7 Harm-domain taxonomies

| Source | Where `clauz3` contributes |
| --- | --- |
| MIT domains | 7.3 robustness, 7.1 own goals and 2.2 security: **B** for effect consequences. 2.1 privacy: **C**. 5.1 overreliance: **mitigated and S6**. 7.6 multi-agent: partial. Domains 1, 3, 4, 6: O. In causal-taxonomy terms, `clauz3` is indifferent to *Entity* and *Intent* and acts at *Post-deployment*, before each action. |
| NIST AI 600-1 | Information Security: **B**. Data Privacy: **C**. Human-AI Configuration: mitigated and S6. Value Chain: **R**. The other eight categories (content, environment, intellectual property, bias, CBRN information): O. |
| IASR | *Malfunctions → reliability* for agents: **B**. *Loss of control*: partial, a containment layer for action-taking systems. *Malicious use*: O, since the user is the principal. |
| EU GPAI Code App. 1.3 | Addresses the affordance "level of human oversight" for tool and computer use, and the characteristic "difficult or impossible to reverse", by deciding before execution. |
| Frontier frameworks | A deployment safeguard for misaligned AI with access to sensitive assets. Not a capability evaluation. |

## 5. Risks ClauZ3 introduces or concentrates

A framework that only lists what a tool covers flatters it. These are the
places where `clauz3` creates or concentrates risk, indexed by the assumptions
in §2.4.

| Assumption | Risk | Current state |
| --- | --- | --- |
| S2 | **Trusted-layer infidelity.** A stub that under-declares effects, or returns quietly on an unknown identifier, voids every proof built on it. | Runtime `requires`/`ensures` checks; `clauz3 lint` with deal markers; [vacuous-proof](../how-to/vacuous-proofs.md) guidance; contradictory-assumption detection. |
| S3 | **Helper-body trust.** `@contract` bodies run as ordinary Python; a helper can return `no_guarantees()` under an environment flag. | Documented; static validation of helper bodies is future work. |
| S2/S3 | **Trusted-layer supply chain.** Installing a library installs trust. | Manifests, operator-authorised layers in the approval service, registry certification (todo). |
| S4 | **Mediation gaps.** Unrestricted harness tools (`--unrestricted`), `--allow-import`, or an un-sandboxed host bypass the gate. | Restricted sessions by default; still needs an OS sandbox. |
| S5 | **Prove/run drift.** Trusted effects do not yet check the approval receipt themselves. | Signed receipts are verified by `clauz3 run`; receipt binding at the effect boundary is open. |
| S6 | **Consent inadequacy.** Users may approve weak contracts, or approval fatigue may move from per-call to per-contract. | Explicit `no_guarantees()`; coverage policies; entailment-based approval policies; plain-language contract descriptions. |
| — | **False atomicity.** "All-or-nothing" refers to authorisation. A runtime failure midway leaves earlier effects in place. | Not addressed; see §6. |
| — | **Utility pressure.** Refusing unsupported Python may push users toward weaker contracts or unrestricted modes. | The subset keeps growing (see the handoff guidance in `AGENTS.md`). |

## 6. What the framework suggests

Read as a roadmap, the lattice and the S-terms point to concrete extensions.
Each one moves a category from O or C toward B, or shrinks an S-term:

1. **Hyperproperties → C to B for disclosure.** Add value labels in the spirit
   of CaMeL and FIDES (source and reader sets) to the fact layer, so that
   contracts can say "no field derived from `read_inbox` reaches a recipient
   outside the thread". Column provenance already exists as a seed. Taint
   tracking turns a 2-safety property into a checkable single-run property
   over labelled facts. Background: [Guardians synergies](../todos/guardians-synergies.md).
2. **Cross-run budgets → Φ_cross-run.** Treat a budget as a persistent fluent
   held by the approval service. Each proof assumes the remaining budget and
   guarantees its own spend, and the service debits it on approval: an
   assume–guarantee composition across programs. This also matches the
   "irreversibility budget" idea in recent agent-control work.
3. **Ordering → Φ_order.** Add an index or happens-before relation to facts,
   for STPA type 3 hazards such as "check balance before paying" or "unlock
   only after authenticating".
4. **Reversibility metadata.** Let manifests tag each trusted effect with
   reversibility and blast radius (read-only, reversible, compensable,
   irreversible). The approval UI and approval policies can then weight
   decisions, and coverage policies can *require* stronger contracts on
   irreversible effects. This links §1's "difficult or impossible to reverse"
   (EU Code) and Shavit §4.2 directly to the vocabulary.
5. **Real atomicity.** Optional buffered or saga-style trusted effects with
   declared compensations would turn all-or-nothing *authorisation* into
   all-or-nothing *execution* for effects that support it.
6. **Shrinking S3 and S5.** Static validation of `@contract` helper bodies, and
   receipt checks inside trusted effects.
7. **Taxonomy-keyed coverage.** Tag coverage policies and contract
   descriptions with the categories they address (`owasp:LLM06`,
   `imda:unauthorised-actions`, `mit:7.3`). An approval card could then say
   which recognised risks a proof bounds, and a library could publish its
   coverage against a standard taxonomy.
8. **An adversarial benchmark.** AgentDojo, InjecAgent and Agent-SafetyBench
   scenarios translated into `examples/*/cases/` would measure the B verdicts
   empirically instead of by argument.

## 7. Summary

| Question | Answer |
| --- | --- |
| What kind of mechanism is `clauz3`? | Sound, fail-closed, static, whole-program enforcement of permutation-invariant single-run trace properties and fluent end states over trusted effects, backed by a boundary execution monitor. |
| Which risks does it bound? | Every risk whose harm is a trusted effect expressible in Φ_bag ∪ Φ_end, *whatever its cause*: excessive agency, rogue or unauthorised actions, tool misuse, and the consequences of injection, poisoning, hallucination or misalignment. |
| Which does it bound partially? | Disclosure (channel, not content); omissions (existential and end-state only); resource use (when modelled as effects). |
| Which are out of scope? | Content harms, confidentiality as a hyperproperty, ordering and timing, cross-run and multi-agent composition, malicious users, model-capability and societal risks. |
| Where does the remaining risk go? | Into six named assumptions: prover soundness, environment fidelity, vocabulary integrity, complete mediation, execution integrity, consent adequacy. None of them involves trusting the model. |

## References

Taxonomies and frameworks:

- Slattery et al. *The AI Risk Repository.* arXiv:2408.12622; *Patterns* (2026), doi:10.1016/j.patter.2026.101517. <https://airisk.mit.edu/>
- NIST. *AI RMF: Generative AI Profile* (NIST AI 600-1), 2024. doi:10.6028/NIST.AI.600-1
- Weidinger et al. *Ethical and social risks of harm from language models.* arXiv:2112.04359; *Taxonomy of Risks posed by Language Models*, FAccT 2022.
- Zeng et al. *AI Risk Categorization Decoded (AIR 2024).* arXiv:2406.17864.
- Hendrycks, Mazeika, Woodside. *An Overview of Catastrophic AI Risks.* arXiv:2306.12001.
- Critch, Russell. *TASRA.* arXiv:2306.06924.
- Bengio et al. *International AI Safety Report* (2025, arXiv:2501.17805; 2026).
- European Commission. *General-Purpose AI Code of Practice*, Safety & Security chapter, 2025.
- Shevlane et al. *Model evaluation for extreme risks.* arXiv:2305.15324.
- OWASP GenAI Security Project. *Top 10 for LLM Applications 2025*; *Agentic AI – Threats and Mitigations* v1.0 (2025); *Top 10 for Agentic Applications for 2026*. <https://genai.owasp.org/>
- Huang. *Agentic AI Threat Modeling Framework: MAESTRO.* CSA, 2025.
- Bryan et al. (Microsoft AI Red Team). *Taxonomy of Failure Modes in Agentic AI Systems*, 2025.
- MITRE. *ATLAS.* <https://atlas.mitre.org>
- Díaz, Kern, Olive. *Google's Approach for Secure AI Agents*, 2025; Google *SAIF* risk map.
- IMDA Singapore. *Model AI Governance Framework for Agentic AI*, v1.0 (2026), v1.5 (2026).
- Chan et al. *Harms from Increasingly Agentic Algorithmic Systems.* FAccT 2023, arXiv:2302.10329.
- Shavit et al. *Practices for Governing Agentic AI Systems.* OpenAI, 2023.
- Hammond et al. *Multi-Agent Risks from Advanced AI.* arXiv:2502.14143.
- Kolt. *Governing AI Agents.* arXiv:2501.07913; 101 Notre Dame L. Rev. (2026).
- Kasirzadeh, Gabriel. *Characterizing AI Agents for Alignment and Governance.* arXiv:2504.21848.
- Feng, McDonald, Zhang. *Levels of Autonomy for AI Agents.* arXiv:2506.12469.
- Willison. *The lethal trifecta for AI agents*, 2025. Meta. *Agents Rule of Two*, 2025.
- Ruan et al. *ToolEmu*, ICLR 2024; Yuan et al. *R-Judge*, 2024; Zhang et al. *Agent-SafetyBench*, arXiv:2412.14470; Debenedetti et al. *AgentDojo*, NeurIPS 2024; Zhan et al. *InjecAgent*, arXiv:2403.02691; Andriushchenko et al. *AgentHarm*, ICLR 2025.
- Lynch et al. *Agentic Misalignment.* Anthropic, 2025; arXiv:2510.05179.
- NIST CAISI. *Strengthening AI Agent Hijacking Evaluations*, 2025; *Security Considerations for AI Agents* RFI, 91 Fed. Reg., Jan 2026.

Formal foundations and defences:

- Schneider. *Enforceable Security Policies.* ACM TISSEC 3(1), 2000.
- Ligatti, Bauer, Walker. *Edit automata.* Int. J. Inf. Sec. 4, 2005.
- Hamlen, Morrisett, Schneider. *Computability Classes for Enforcement Mechanisms.* ACM TOPLAS 28(1), 2006.
- Alpern, Schneider. *Defining Liveness.* IPL 21(4), 1985. Clarkson, Schneider. *Hyperproperties.* JCS 18(6), 2010.
- Necula. *Proof-Carrying Code.* POPL 1997.
- Saltzer, Schroeder. *The Protection of Information in Computer Systems.* Proc. IEEE 63(9), 1975.
- Leveson. *Engineering a Safer World.* MIT Press, 2012; Leveson, Thomas. *STPA Handbook*, 2018.
- Dalrymple et al. *Towards Guaranteed Safe AI.* arXiv:2405.06624.
- Beurer-Kellner et al. *Design Patterns for Securing LLM Agents against Prompt Injections.* arXiv:2506.08837.
- Debenedetti et al. *Defeating Prompt Injections by Design (CaMeL).* arXiv:2503.18813.
- Costa et al. *Securing AI Agents with Information-Flow Control (FIDES).* arXiv:2505.23643.
- Shi et al. *Progent.* arXiv:2504.11703. Wang, Poskitt, Sun. *AgentSpec.* arXiv:2503.18666. Zhu et al. *MiniScope.* arXiv:2512.11147.
- Tsai, Bagdasarian. *Contextual Agent Security (Conseca).* arXiv:2501.17070. Bagdasarian et al. *AirGapAgent.* CCS 2024.
- Chen, Kang, Li. *ShieldAgent.* ICML 2025. Xiang et al. *GuardAgent.* arXiv:2406.09187. Wu et al. *IsolateGPT.* NDSS 2025.
- Christodorescu et al. *Systems Security Foundations for Agentic Computing.* arXiv:2512.01295.
- Dantas et al. *Toward Safe LLM Agents: A Survey of Specification, Verification, and Enforcement.* arXiv:2608.14590.

!!! note "Verification status"
    Category names and counts were checked against primary sources where they
    were accessible in October 2026. The ASI01–ASI10 names come from secondary
    summaries because the official PDF is gated. The 2026 arXiv preprints cited
    here are not peer-reviewed.
