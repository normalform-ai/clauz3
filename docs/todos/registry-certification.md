# Registry certification for trusted layers

Status: design proposal, 2026-10-12. Extends the signed/attested install model
in [issue #47](https://github.com/cmungall/agent-deal/issues/47). The current
registry is described in [Registry](../registry.md).
This note is intentionally outside the MkDocs navigation.

## Goal

Let third parties, both agents and humans, certify installable trusted layers,
and let consumers require those certifications before a layer's guarantees are
accepted. Certification should cover both correctness and softer properties
such as whether contract names are honest and aligned with shared vocabulary.

Installing from any git repository must keep working. The registry is a trust
overlay on top of `clauz3 install gh:...`, not a gate in front of it.

## Why certification matters more here than in a package index

On a general package index, certification mostly means "probably not
malicious". In clauz3 the trusted layer defines the meaning of user consent.
When a user approves a plan carrying `@clauz3.guarantee(only(recipients=...))`,
they rely on two things the prover cannot check:

1. **The effect body matches its spec.** The prover never executes trusted
   effect bodies. This is the largest soundness gap and the most valuable
   thing to certify.
2. **The contract name matches its logic.** `only`, `at_most`, and
   `dry_run_only` must mean what a human reading an approval would assume.

The certification target is also small and well defined: the
`tools/<domain>/trusted/` tree plus its bundled tests (see
[Distributed trusted layers](../explanation/distributed-trusted-layers.md)).
Careful review is realistic in a way it rarely is for third-party code.

## Design

### Separate the index from attestations

- **Index entry**: a name mapped to `source@sha` plus a content hash of the
  installed `trusted/` tree (`tree_hash`). Curated names prevent squatting;
  each name resolves to a pinned hash.
- **Attestation**: a signed statement about one `tree_hash`, never about a
  repository, branch, or tag. This follows the cargo-crev and in-toto model.
- **Consumer policy**: decides which signers and claim kinds are required.
  Anyone may publish attestations; consumers choose whom to trust.

Unregistered sources still install. They simply carry no attestations, and a
consumer policy may or may not accept that.

### Claim kinds

Claims split into mechanical ones, which are reproducible and suit agents and
CI, and judgment ones, which need human review (agent-assisted).

| Claim | Certifier | Evidence |
|---|---|---|
| Pass cases prove, fail cases do not; strict mypy; subset compliance | agent / CI | deterministic, rerunnable report |
| Every contract has a fail case it rejects (no vacuous contracts) | agent | mutation-style checks over contracts |
| Contracts are satisfiable by some accepted program | agent | witness pass case per contract |
| Effect body matches its spec | agent + human | [differential testing](../explanation/differential-testing.md) plus code review |
| Contract names are honest; vocabulary is aligned | human (agent-assisted) | review against canonical semantics |
| Diff from the previously certified hash is acceptable | human | diff review; cheap because layers are small |

### Mechanized vocabulary alignment

Contracts are logic, not prose, so alignment can be partly proved rather than
merely reviewed. The stdlib could ship canonical definitions of shared terms
(`only`, `none`, `at_most`, `unique_*`, ...). A library exporting one of those
names must be proved equivalent to the canonical definition with Z3, under a
declared mapping from its effect onto the canonical effect shape. If the
equivalence fails, the library must rename the contract.

Open questions: how to state the effect mapping, and whether one-directional
implication (the library's contract is at least as strong) is acceptable for
some terms.

### Receipts name the certified hash

Following #47, an installed layer carries a manifest recording
`source: gh:org/repo@<sha>` and its `tree_hash`. A proof receipt records the
`tree_hash` and the attestations that satisfied the consumer policy at approval
time, so an approval remains auditable later.

### Consumer policy

Policy plugs into the existing approval policy machinery
([approval policies](approval-policies.md)). Examples:

- require one human semantic review and a passing mechanical report for any
  layer whose effects send email or write outside the project;
- accept mechanical-only certification for read-only layers;
- refuse layers whose `tree_hash` has no attestation from a listed signer.

## Risks

- **Stale certifications.** Certifying a hash is correct, but every upgrade
  drops certifications. Good diff-review tooling between certified hashes is
  what makes this workable.
- **Prompt injection of agent certifiers.** A certifying agent reads untrusted
  repository content: docstrings, READMEs, test names. Its signed output should
  be the mechanical report plus structured findings, not a verdict derived
  from prose in the repository.
- **Rubber-stamping and reputation.** Show who certified what and which claim
  kinds. Do not collapse attestations into a single badge.
- **Centralization.** If the registry becomes the only install path, the
  "local trust, portable checks" property is lost. Open attestation publishing
  with consumer-chosen signers avoids this.
- **Namespace squatting.** Curated names resolving to pinned hashes.

## Incremental path

1. Add `sha` and `tree_hash` to `library_catalog.json` entries; `install`
   verifies the hash when installing a registered name.
2. Add `clauz3 verify <lib>`: run every mechanical check and emit a JSON
   report. This report is exactly what an agent certifier signs.
3. Define an attestation format. Git/SSH signatures are probably sufficient,
   since installation already relies on git authentication. Store attestations
   in a separate registry repository as
   `attestations/<tree_hash>/<signer>.json`.
4. Add consumer-side policy to `approvals/` and record `tree_hash` plus
   satisfying attestations in receipts.
5. Add the canonical vocabulary module and the equivalence check.
