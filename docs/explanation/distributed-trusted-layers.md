# Distributed Trusted Layers

One underappreciated benefit of `clauz3` is that a trusted layer can ship as a
small, testable package. The static checker is the headline, but the packaging
shape matters even when you ignore Z3 for a moment: an agent tool should carry
its own API, contracts, examples, and tests together.

That is the stdlib convention:

```text
<tool>/
  clauz3.yaml               # optional package-level shared requirements
  README.md
  tools/<tool>/
    clauz3.yaml             # domain description and prerequisites
    trusted/
      effects.py
      contracts.py
  tests/
    Justfile
    cases/
```

The installable artifact is the whole `tools/<tool>/` directory; only its
`trusted/` subtree is a trusted Python boundary. `clauz3 install stdlib:db`
copies the domain code and metadata into a project. An optional package
manifest's shared requirements are snapshotted beside the installed domain
manifest. Root-level tests and guides remain in the source distribution;
`clauz3 test stdlib:db` explicitly runs that source's bundled test suite.
See [Library manifests and setup](../reference/library-manifests.md), including
the warning for legacy sources without domain manifests. An absent optional
package manifest is not a warning.

## Why this matters

Agent tools are usually distributed as instructions, code snippets, or API
wrappers. The receiving project often has to infer what counts as correct use,
which inputs are safe, and whether the examples still match the implementation.
That creates drift:

- a skill describes an operation, but the executable tool has changed
- a README shows one usage, but the policy surface accepts another
- a project installs a tool, but does not inherit the tests that made it safe
- a runtime wrapper works, but no one checks the permission vocabulary

`clauz3` gives the tool author one place to define the boundary. The trusted
effect says what the outside-world operation is. The contract module says what
claims an agent may make about it. The tests prove that those claims accept and
reject the expected programs.

## Tests as part of the distribution

A trusted layer test suite is not just a project maintainer convenience. It is
part of the artifact's meaning.

For proof-oriented tests, pass cases should prove and fail cases should not:

```text
tests/cases/
  only_table_pass.py
  only_table_fail.py
  reads_at_most_pass.py
  reads_at_most_fail.py
```

The Justfile encodes those expectations. This makes a stdlib tool cheap to
audit after installation or upgrade:

```bash
clauz3 test stdlib:db
```

Because proof tests do not execute trusted effect bodies, they can safely cover
real side-effecting tools such as filesystem, web fetch, or database access.
When a tool also needs runtime fixtures, the suite can generate them fresh each
run, as `stdlib:db` does for CSV, Parquet, and DuckDB smoke data.

## Skills and trusted layers

Skills are useful because they tell an agent how to use a domain. Trusted
layers are useful because they define what the agent is allowed to call and
what it can prove. Keeping those together is the better unit of reuse.

In that model, a distributed tool has four audiences:

| Audience | Artifact |
| --- | --- |
| Agent | skill instructions and examples |
| Prover | trusted effects and contract helpers |
| User | concise proved guarantees |
| Maintainer | bundled tests and runtime fixtures |

This avoids treating the skill as prose floating above an unrelated codebase.
The skill can point at the same contracts the prover checks and the same cases
the maintainer runs.

## Local trust, portable checks

Installing a trusted layer does not mean blindly trusting an upstream project
forever. The layer is small enough to inspect, and the tests are portable enough
to rerun in the receiving environment.

That is a different posture from a centralized tool registry. A registry can
say a tool exists. A `clauz3` layer can also say:

- here are the side effects this tool performs
- here is the contract vocabulary users can consent to
- here are the proofs that should pass
- here are the proofs that should fail
- here is the runtime smoke data, generated locally

The static proof is still the reason to use `clauz3` for high-stakes agent
effects. But the distribution story is useful on its own: tools become small,
auditable, reproducible units instead of loose bundles of instructions and
helpers.
