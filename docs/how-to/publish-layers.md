# Publishing Trusted Layers

This guide is for the author of a *domain trusted layer*: a
`tools/<domain>/trusted/` package that lives in its own repository, possibly
private, and is pulled into consumer projects with `clauz3 install`. The
bundled [standard library](../reference/stdlib.md) is deliberately small;
richer domains with their own release cadence, a larger surface area, or
proprietary vocabularies (instrument schemas, vendor APIs) belong in a separate
repository. The first such repository is
[`clauz3-tools-autolabs`](https://github.com/normalform-ai/clauz3-tools-autolabs);
the plan behind it is
[agent-deal issue #41](https://github.com/cmungall/agent-deal/issues/41).

The consumer side (`clauz3 install`, `gh:` sources, the sha-keyed cache, the
`.clauz3/` project layout) is covered in
[Installing trusted layers](install-layers.md), and the reasons for shipping a
layer with its own tests are in
[Distributed trusted layers](../explanation/distributed-trusted-layers.md).
This page covers what the layer repository must look like, how to name it so it
cannot collide with another layer, and what its maintainer is promising.

## Repository layout

Use the stdlib shape. It is what `clauz3 install` and `clauz3 test` resolve:

```text
clauz3-tools-<umbrella>/
  README.md                 # what the layer is for; install line; API at a glance
  AGENTS.md                 # how an agent should use the layer
  LICENSE
  tools/
    <org>/<domain>/
      trusted/
        effects.py          # @clauz3.trusted effect functions with preconditions
        contracts.py        # @contract helpers over those effects
        policy.py           # optional: POLICY = domain_policy(...) for coverage
  tests/
    Justfile                # lint + every case; run by `clauz3 test`
    cases/
      <contract>_pass.py    # must prove
      <contract>_fail.py    # must not prove
```

One repository may hold several domains side by side (`tools/<org>/lims/`,
`tools/<org>/madsci/`). `clauz3 install` copies every domain it finds, so split
domains across repositories when consumers should be able to install them
independently, and keep them together when they are always used as a set.

A domain is found by one rule, shared by `install` and by every command that
discovers trusted roots on its own (`clauz3 run`, `clauz3 tools`,
`clauz3 policy-check`, `clauz3 start`):

- it is a `trusted/` directory at `tools/<domain>/trusted/` or
  `tools/<org>/<domain>/trusted/`; anything deeper is skipped with a warning;
- every directory name on the way is a Python identifier, because it becomes
  part of the import name. `tools/acme-labs/lims/trusted/` cannot be imported,
  so it is skipped with a warning (and an install whose only domains are
  skipped fails). Use `acme_labs`, not `acme-labs`, even when the GitHub org
  has a hyphen;
- it is not a symlink and is not reached through one;
- a `trusted/` directory nested inside another domain's `trusted/` is part of
  that domain, not a second one.

## Choose a name that cannot collide

This is the decision to make first, because it appears in every import line.

The prover imports a trusted layer through Python's import system under the
dotted name of its directory: `.clauz3/tools/lims/trusted/` becomes
`tools.lims.trusted`, whether it was installed under `.clauz3/tools/` or
authored under the project's own `tools/`. Two different layers with the same
dotted name therefore collide. Within one project, `install` refuses to
overwrite an existing `.clauz3/tools/<domain>/` without `--force`, and refuses a
domain the project already authors. Across projects in one process (a
long-running approval service, a test suite proving against several projects)
the loader refuses a module already loaded from a different file rather than
proving against the wrong one
([agent-deal issue #111](https://github.com/cmungall/agent-deal/issues/111)).
Either way the collision is an error, not a silent swap, but it is still an
error your consumers hit.

A layer author has two options:

1. **Namespace the domain under your org** (recommended):
   `tools/<org>/<domain>/trusted/`, for example
   `tools/normalform/lims/trusted/`. Consumers then write
   `from tools.normalform.lims.trusted import contracts`. Nested domains are
   supported end to end: `install` copies them to
   `.clauz3/tools/<org>/<domain>/`, `--skills` writes
   `.clauz3/skills/<org>/<domain>/SKILL.md`, `prove --trusted-root` proves
   against them, and `run`, `tools`, `policy-check` and `start` discover them
   without an explicit `--trusted-root`. `clauz3 tools` lists each effect by
   its path below `tools/` (`normalform/lims/trusted/effects.py:pipette`), so a
   namespaced domain and a flat one of the same name are distinguishable.
2. **Use a flat name that is provably unique.** Never reuse a bundled stdlib
   name (see [Standard library tools](../reference/stdlib.md)) or an
   `examples/` domain name. A flat name is shorter to import but you own the
   uniqueness check.

Whichever you choose, the name is part of the layer's public interface:
renaming it later breaks every consumer's imports and generated skills.

## Effect names are global across layers

Org namespacing separates *modules*, not *effects*. A contract addresses an
effect by its bare function name (`effect("pipette")`), and so does a coverage
policy's `when_used`. Every trusted root in a proof therefore shares one effect
namespace: if `tools/acme/lims/trusted/effects.py` and
`tools/other/lims/trusted/effects.py` both define `pipette`, proving with both
roots is refused with "two trusted effects are called 'pipette'" (and
`clauz3 run`, which discovers every installed root, refuses too). A consumer
then has to choose between your layer and the other one.

So treat effect function names as interface:

- prefix effect names with the domain when they are generic
  (`lims_pipette`, not `pipette`; `madsci_move`, not `move`);
- keep helper functions that are not effects out of `@clauz3.trusted`, so they
  do not occupy the shared namespace;
- if you add `@deal.has(...)` markers for deal's runtime guard and
  `clauz3 lint`, keep them honest about the worst case the body can do. Markers
  no longer address anything in a contract or policy, but `lint` checks the
  body against them.

## Packaging: a plain git tree, not a wheel

Ship the layer as a git repository, not as a Python package on an index.

- The installable artifact is a *directory*. `install` copies it into the
  consumer's `.clauz3/tools/`, rejects symlinks in it, and refuses to clobber an
  existing domain. A wheel would bypass all three checks.
- The prover imports the layer as `tools.<...>.trusted` relative to the
  consumer's import roots, and the untrusted-import allowlist admits only that
  prefix. A wheel would introduce a second import name (`import acme_lims`)
  that agent programs may not use.
- Git already gives versioning (tags), pinning (shas), and access control
  (private repositories with your existing SSH or credential-helper auth). The
  provenance manifest and signing that would make a pinned install attestable
  are tracked in
  [agent-deal issue #47](https://github.com/cmungall/agent-deal/issues/47); this
  guide does not define a competing scheme.

The one Python package in the picture is `clauz3` itself. State the minimum
`clauz3` version your layer's contracts need in `README.md`; the layer
repository needs no `pyproject.toml` unless its `tests/` use extra tooling.

## What the maintainer guarantees

The effect bodies *are* the security boundary: the prover never executes them,
and `clauz3 run` executes them after a program is proved and approved. A
consumer who installs your layer is trusting these statements about it:

- Every effect does only what its name, docstring and preconditions say, and
  any `@deal.has` markers name everything the body can actually do.
- Preconditions (`@clauz3.requires`) fail closed: a call the prover cannot show
  satisfies them does not prove.
- Effects that reach the outside world (mail, orders, actuators) default to a
  dry run, `*, dry_run: bool = True`, with a contract a policy can demand. The
  rationale is in `docs/todos/clauz3-tools-assistant.md`; the mechanism is
  locked in by `tests/test_dry_run_kwarg.py`.
- The tree contains no symlinks, and every directory on the way to `trusted/`
  is a Python identifier.
- Every contract has at least one `_pass` and one `_fail` case, and
  `clauz3 test` is green at every tagged release.
- Tags are never moved. Consumers pin shas; tags are for humans.
- For a private layer: the README states who may install it and how access is
  granted, because `clauz3` itself has no notion of access beyond git's.

## Tests

Start from a stdlib `tests/Justfile` (for example
`src/clauz3/stdlib/filesystem/tests/Justfile`) and edit the paths. A minimal
template:

```just
# `clauz3 test` overrides CLAUZ3 so the suite runs from an installed wheel
# as well as from a source checkout.
clauz3 := env_var_or_default("CLAUZ3", "uv run clauz3")

trusted := "../tools/acme/lims/trusted"
prove := clauz3 + " prove --trusted-root " + trusted + " --import-root .."

test: lint cases

lint:
    {{clauz3}} lint {{trusted}}/effects.py

cases: only-plate-pass only-plate-fail

only-plate-pass:
    {{prove}} cases/only_plate_pass.py

only-plate-fail:
    if {{prove}} cases/only_plate_fail.py; then exit 1; fi
```

Conventions that make the suite meaningful:

- `_pass.py` cases must prove and `_fail.py` cases must be refuted, one pair
  per contract, plus a `<domain>_no_guarantees_pass.py` that shows the null
  contract proves.
- A `_fail` recipe written as `if {{prove}} ...; then exit 1; fi` also passes
  when `prove` exits non-zero for the wrong reason, such as a configuration
  error. Where a refutation is load-bearing, assert the conclusion in a pytest
  test instead.
- Proof tests never run effect bodies, so they are safe for real
  side-effecting tools. If you add a runtime smoke recipe, keep it separate and
  make it generate its own fixtures, as `stdlib:db` does.
- For a repository with several domains, use one `tests/Justfile` with a recipe
  per domain and make `test` depend on all of them;
  `clauz3 test <source> --recipe <domain>` runs one.

Run the suite the way a consumer will:

```bash
clauz3 test .                                          # from the layer repo
clauz3 test gh:<org>/clauz3-tools-<umbrella>@<sha>     # from anywhere, before trusting it
```

## Consuming a published layer

```bash
# Look before you trust: runs the layer's own suite from the sha-keyed cache.
clauz3 test gh:<org>/clauz3-tools-<umbrella>@<sha>

# Install, pinned to a sha, with a generated SKILL.md per domain.
clauz3 install gh:<org>/clauz3-tools-<umbrella>@<sha> --skills

# prove takes its trusted roots explicitly; it does not discover them.
clauz3 prove --trusted-root .clauz3/tools/<org>/<domain>/trusted plan.py

# run, tools, policy-check and start discover installed domains, nested or flat.
clauz3 tools
clauz3 run plan.py
```

Commit the installed `.clauz3/tools/` and `.clauz3/skills/` into the consumer
repository; the install is a copy, not a link, so the consumer's proofs do not
change when the upstream moves. Upgrade deliberately with `--force` after
re-running `clauz3 test` against the new sha. Nothing yet records which sha a
project install came from; until
[agent-deal issue #47](https://github.com/cmungall/agent-deal/issues/47) lands,
keep the install line with its sha in the consumer's README or Justfile.

## Publishing checklist

1. Name: `clauz3-tools-<umbrella>` for the repository,
   `tools/<org>/<domain>/` inside, with identifier-only directory names.
2. Effect function names are prefixed where they are generic.
3. `clauz3 test .` is green, including `clauz3 lint`.
4. `clauz3 install . --into "$(mktemp -d)" --skills` copies exactly the domains
   you intend, prints no "skipping trusted layer" warning, and the generated
   `SKILL.md` reads sensibly.
5. `README.md` states the install line with a sha, the minimum `clauz3`
   version, and (for a private layer) how access is granted.
6. Tag the release; never move the tag.
7. Optionally add a row to the [Registry](../registry.md) by pull request;
   private layers are listed as "available on request".
