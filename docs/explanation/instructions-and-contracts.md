# Instructions are not controls

A user who tells an agent "only email Bob" has said what they want. They have
not constrained what the agent does. Two lines of research describe how the
gap opens. People give agents instructions that are vaguer and less complete
than they realise, and agents that read external content take instructions
from whoever wrote that content, not only from the user.

`clauz3` puts the control on the other side of the gap. The user still
instructs the agent in natural language, but what they approve is a contract
the agent proposes and the prover checks. This page reads both research
results from that angle: what each problem means for a contract layer, what
`clauz3` already does about it, and what it leaves open.

## Users under-specify

Li et al. (2020) asked 58 people, most of them non-programmers, to tell a
phone agent how to carry out everyday tasks that behave differently under
different conditions, such as playing different music depending on the time
of day. Their formative study found three patterns.

**Vague concepts.** A third of participants used conditions with no clear
definition, such as "if it is daytime, play upbeat music". They did this even
though every example they had been shown used precise conditions. When
participants could see an app screenshot showing the relevant value, the rate
fell to 9%. Concrete values grounded the concept.

**Missing else branches.** 18% of descriptions left out an else branch that
the task called for. Another 33% left out one that a person would infer: "if a
public transport stop is more than half a mile away, request an Uber" leaves
"otherwise take the bus" unsaid.

**Assumed common sense.** Participants expected the agent to know what
"weekend" or "sunset time" meant and how to find out.

The authors' system, PUMICE, closes these gaps through dialogue. It asks what
"hot" means, asks what to do otherwise, and lets the user define a concept by
pointing at it in an app. Information flows from user to agent until the
instruction is precise enough to execute.

### What this means for a contract layer

`clauz3` accepts that the instruction will be imprecise and lets the agent fill
the gaps. Each gap the agent fills becomes code, and the user sees the bound on
that code before anything runs. The precision moves into the contract.

- **The vocabulary is precise even when the request is not.** Contracts are
  built from `@contract` predicates written and reviewed in the trusted layer.
  Their approval text comes from
  [trusted description templates](../reference/contract-descriptions.md),
  never from the submitting agent or a language model. A user who said "the
  notes file" approves `only_reads_files([FilePath("notes.md")])`, rendered as
  a fixed sentence about exact files. Where the older
  `only_read_under("notes.md")` would have quietly allowed `notes.md.bak`, the
  typed path makes the boundary explicit.
- **The approval dialog grounds the request in concrete values.** It shows
  literal arguments: the address list, the file paths, the spend limit. This
  mirrors the screenshot finding in Li et al.: the user judges specific values
  instead of their own paraphrase.
- **The prover covers every branch.** A missing else in the request is
  harmless if the agent fills it sensibly and harmful if it does not, but the
  guarantee holds on the branch the user forgot as much as on the one they
  described. The user does not have to anticipate the branch for the bound to
  apply.
- **Missing guarantees are the contract-level version of a missing else.** An
  agent can attach a strong guarantee to email and none to the database it
  also writes. [Coverage policies](../reference/coverage-policies.md) let the
  trusted layer recommend or require guarantees for each domain a program uses,
  so the approval dialog shows a domain with no guarantee instead of hiding it.

One gap stays open. PUMICE grounds "it's hot" in a value the agent can read.
`clauz3` can prove that every thermostat call targets the zone named
`"Dining Room"`, but not that a zone with that name exists. That is the
referential gap described in [Avoid vacuous proofs](../how-to/vacuous-proofs.md).
The trusted layer has to close it at run time: an effect that resolves a name
against external state must raise when nothing matches.

## Someone else is giving instructions

Greshake et al. (2023) introduced *indirect prompt injection*. An application
that feeds retrieved content into a model erases the line between data and
instructions. Anyone who can place text where the model will read it, such as
a web page, an email, a code comment or a document, can steer the model
without ever talking to it. They demonstrated this against Bing Chat,
code-completion tools and synthetic GPT-4 applications. Their taxonomy of
outcomes includes data theft, injections that spread from one system to
another, persistent compromise, and, most relevant here, control over "how
and if other APIs are called". Writing the attacks took little effort; their prompts
often worked on the first attempt.

Their assessment of defenses is bleak. Fixes from training arrive one jailbreak
at a time. A filter model that reads the retrieved input to look for
injections can fall into the same trap. A weaker filter that cannot be
instructed also cannot decode an obfuscated payload. They conclude that "it is
currently hard to imagine a foolproof solution".

### What this means for a contract layer

`clauz3` does not stop the agent from being fooled (see the
[FAQ](faq.md#does-this-prevent-prompt-injection-or-other-prompt-exploits)).
What it changes is how much a fooled agent can do. Injected text can reach a
`clauz3` program at two points, and the protection differs between them.

| Injected text arrives… | It can… | What bounds it |
| --- | --- | --- |
| while the agent is **writing** the program | change the code and the proposed contract | The user approves the contract, so a weakened guarantee or an explicit `no_guarantees()` is visible before anything runs. |
| while the proved program is **running**, as data returned by a trusted effect | change which branch runs and which values flow where | The proof covers every branch, so no input can push the program past its guarantees. |

The second row is where the contract layer earns its keep. Greshake et al.
treat injected text as arbitrary code running in the model. In a proved
program, data returned at run time can only choose among behaviours the
contract already allows. The [malicious-repo guide](../how-to/malware-repo-defense.md)
works through a direct case: an issue comment tells the agent to download and
run an archive. Two contracts there, one restricting which comments may be
read and one restricting where download URLs may come from, make the injected
link fail to prove, whether it shows up as a literal in the code or as a value
in a comment from an untrusted author.

The checking side also avoids the trap that worries Greshake et al. No
language model reads the input or checks the contract. The checker is Z3,
run against trusted predicates, and injected text cannot instruct it.

What remains open:

- **Authoring-time injection still works.** An agent persuaded to propose a
  harmful program with an honest but weak contract is caught only if the user
  reads the contract. Coverage policies and `no_guarantees()` make weakness
  visible; they do not make users read it.
- **Confidentiality.** `clauz3` contracts bound effects and data flow into
  effects. Tracking which values a reader may see, which is the exfiltration
  side of the Greshake taxonomy, is where [CaMeL](background.md#camel) goes
  further.
- **The trusted layer must be honest.** Every bound above assumes the trusted
  effects declare what they actually do. See the
  [environment contract](background.md#environment-contract).

## Where this leaves instructions

Instructions still matter. A clear request gets the agent to propose the right
program and a contract worth approving on the first try. But both studies
point to the same conclusion. The user's words are an imprecise
specification, and they reach the model in the same channel as text from
anyone else. A contract layer treats the instruction as a request for a
proposal, and treats the checked contract as the thing that grants
permission.

## References

- Greshake, K., Abdelnabi, S., Mishra, S., Endres, C., Holz, T., & Fritz, M.
  (2023). *Not what you've signed up for: Compromising real-world
  LLM-integrated applications with indirect prompt injection.* Proceedings of
  the 16th ACM Workshop on Artificial Intelligence and Security (AISec '23).
  [doi:10.1145/3605764.3623985](https://doi.org/10.1145/3605764.3623985),
  [arXiv:2302.12173](https://arxiv.org/abs/2302.12173)
- Li, T. J.-J., Radensky, M., Jia, J., Singarajah, K., Mitchell, T. M., &
  Myers, B. A. (2020). *Interactive task and concept learning from natural
  language instructions and GUI demonstrations.* AAAI-20 Workshop on
  Intelligent Process Automation.
  [arXiv:1909.00031](https://arxiv.org/abs/1909.00031)
