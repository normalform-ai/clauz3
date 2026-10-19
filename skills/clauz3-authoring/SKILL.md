---
name: clauz3-authoring
description: >-
  Design or extend a Clauz3 trusted library when agents need capabilities or
  safety contracts that existing libraries do not provide. Define trusted
  effects, typed results, preconditions, contracts, shared events, runtime
  obligations, tests, and installable metadata. Use for library authoring,
  not for writing agent programs against an already installed library.
---

# Authoring a clauz3 trusted layer

There's a domain — labs, mail, files, finance, your own system —
where an agent needs to act, and either (a) no trusted layer for it
exists, or (b) the existing layer doesn't yet express the safety
properties this use case requires. Without a trusted layer, the only
options are to hand the agent unrestricted access (rarely safe) or to
not act at all (often unhelpful). Authoring a trusted layer is the
third option: a small audited boundary the agent passes through, with
a contract vocabulary rich enough to let the user say yes.

You're in the **Trusted Layer Engineer** role
(see [trust boundary](#trust-boundary)). You're not writing
agent code or proving anything; you're writing the *thing the prover
trusts*. If your effects are wrongly marked, or your contracts admit
predicates they shouldn't, every proof against this layer is wrong.
Treat this as security-critical work — closer to writing a Linux
syscall than writing a library.

For *using* an installed trusted layer (writing agent plans against
one), see the companion **clauz3** skill.

## Using this skill outside the Clauz3 repository

Copy or install the **whole `clauz3-authoring/` directory**, including
`references/`. Relative Markdown links stay inside that directory. Source paths
shown as code (such as `src/clauz3/stdlib/db/`) refer to a matching Clauz3 source
checkout, not the consumer project. The portable references below provide the
patterns without requiring that checkout:

- [Returning typed data](references/returned-data.md): result annotations,
  `clauz3.Row`, generic schemas, size bounds, and caller limitations.
- [Sharing filesystem events](references/shared-filesystem-events.md): reuse
  filesystem contracts for another library's file operations.
- [Library metadata](references/library-metadata.md): find the installed LinkML
  schema and validate manifests without a repository `docs/` directory.

## Anatomy of a trusted layer

A complete library has this shape:

```
<library>/
├── clauz3.yaml            # optional kind: package; shared prerequisites
├── tools/<domain>/
│   ├── clauz3.yaml        # library-owned human description and requirements
│   ├── README.md          # optional installed human-facing guide
│   └── trusted/
│       ├── effects.py     # trusted side-effecting functions
│       └── contracts.py   # contract vocabulary over the effect facts
├── tests/
│   ├── Justfile           # proof recipes for `clauz3 test`
│   └── cases/             # *_pass.py must prove; *_fail.py must not
├── README.md
└── AGENTS.md              # consumer-facing guide
```

`clauz3 install` copies the entire `tools/<domain>/` directory, including its
manifest and documentation. Only `trusted/` is a trusted Python boundary.
Repository-level tests and guides remain in the source repository.

## Library description and user setup

Put `clauz3.yaml` beside `trusted/`, one manifest per domain. Its `name` must
match the domain path below `tools/`. Write for a person deciding whether to
enable the library: `summary` supplies the library card, and `description`
explains its capabilities and limits. The registry contains source locations
only; do not duplicate descriptions there.

When domains share prerequisites, optionally put `clauz3.yaml` at the source
root with `schema_version: 1`, `kind: package`, `name`, `title`, and
`requirements`. An optional `documentation` URL can point to shared setup.
Keep domain descriptions, dependencies, and effect-specific permissions in
`tools/<domain>/clauz3.yaml`. The package manifest neither lists domains nor
changes imports or trust. All domains in a package must have their own manifest.

The installer copies shared metadata into a generated `.clauz3-package.yaml`
beside each installed domain manifest, leaving the authored manifest unchanged.
Do not hand-maintain these snapshots. They make installed copies self-describing;
update the root manifest and reinstall with `--force --skills` to refresh them.
There is no second package install tree or symlink facade. Repository-root
tests, examples, and guides remain at the source revision.

Shared requirements are additive: identical Python specifiers are deduplicated,
tools are keyed by `name`, and setup steps by `id`. Identical keyed entries
deduplicate; conflicting definitions fail installation instead of overriding
one another. Keep `applies_to` in domain manifests. For gog, share CLI installation
and account selection while keeping Gmail-specific authorization in email's
manifest. Shared setup is descriptive, not an authentication check.

```yaml
schema_version: 1
name: email
title: Gmail
summary: Save Gmail drafts or send email through your own account.
description: |
  Prepare unsent Gmail messages or deliver new emails, with contracts that
  restrict recipients, content, and message counts. Previews save nothing.
requirements:
  tools:
    - name: gog
      description: The gog CLI must be available to Clauz3.
      instructions: Install gog and put it on the PATH used to launch Clauz3.
      documentation: https://gogcli.sh/quickstart.html
  setup:
    - id: gmail-oauth
      kind: authentication
      title: Authorize Gmail access
      description: Your Google account must grant gog the required Gmail access.
      instructions: |
        Follow the gog quickstart to configure OAuth and authorize your account.
        Keep credentials and tokens in gog's storage, not in this manifest.
      documentation: https://gogcli.sh/quickstart.html
      permissions:
        - Gmail access for drafts and sending
      applies_to:
        - create_draft
        - send_email
```

Use `requirements.python` for Python package specifiers, `requirements.tools`
for external executables, and `requirements.setup` for user-managed
`authentication`, `configuration`, `service`, or `access` prerequisites.
Declare other Clauz3 library source references in top-level `dependencies`.
Do not treat a Python package install as sufficient for OAuth, account
selection, service availability, or organization permissions. Explain which
operations need setup and whether previews work without it. Give recovery
guidance for revoked access where relevant. Never store secret values or
executable installation hooks in manifests; setup instructions are displayed,
not run. Do not claim readiness merely because a library was installed.

The portable [metadata reference](references/library-metadata.md) explains how
to read the LinkML schema shipped with Clauz3 and run its generated validator.
Test a new manifest with installation into a temporary consumer project:

```bash
clauz3 install /path/to/library --into /path/to/test-project --skills
```

Installation validates the manifest before copying it. Test discovery and
installation without importing library code or executing any setup command.
Legacy sources with neither manifest still install, with a non-blocking CLI
warning and Libraries review notice for each missing domain manifest. Their API
skills still generate, but have no manifest overview or setup guidance. A missing
root package manifest is normal and produces no metadata warning. If a package
manifest is present, every discovered domain must have a valid domain manifest;
missing ones fail before copying, as do malformed manifests and conflicting
requirements. New libraries should ship domain manifests. Reinstall after
changing descriptions so consumers get the matching metadata and code.

## Provider bundles with separate services

A bundled source need not have the same name as its domains. For example,
`stdlib:google_workspace` installs the package at
`src/clauz3/stdlib/google_workspace/`, whose shared `clauz3.yaml` owns the
**Google Workspace** title, gog setup, and account selection. Gmail lives at
`tools/google_workspace/gmail/`; its manifest name is `google_workspace/gmail`
and its title is **Gmail**. Python imports use
`tools.google_workspace.gmail.trusted`.

Add future services as siblings (`tools/google_workspace/sheets/`, for example),
each with its own `trusted/` and `clauz3.yaml`. Discovery derives the domain list
from the trusted roots; do not duplicate it in the registry or package manifest.
Keep service contracts independent, and don't declare sibling services as
required dependencies unless they are genuinely necessary. Installation copies
the bundle, while workspace conversation selection remains per service.

The old `email` domain is superseded. Move it out of active tools before
installing the bundle and update imports; do not ship a duplicate compatibility
copy with the same effect names. Mock `examples/email` remains a separate teaching
example and retains its own imports.

## effects.py: the side-effecting boundary

Trusted effects are decorated with `@clauz3.trusted` and optionally
`@clauz3.requires(...)` preconditions:

```python
import clauz3


@clauz3.requires(lambda addr, msg: "@" in addr, message='addr must contain "@"')
@clauz3.trusted
def send_email(addr: str, msg: str) -> None:
    """The body runs only under `clauz3 run`, after proof + approval."""
    # ... real implementation, or `pass` for a mock layer
```

Three things to internalise:

1. **`@clauz3.trusted` takes no arguments.** A function either is the
   boundary or it is not, and its *name* is how a contract addresses it:
   `effect(send_email)` with the function imported. To span several, name them —
   `effect(write_file, append_file)` — and to span every trusted call
   in a program, `effect()`. Keep the grouping in the contract that needs
   it rather than in a string on the declaration, so a later effect cannot
   silently join a relation the contract already constrains.

2. **`@clauz3.requires(lambda ...)` is the per-call precondition.** The
   prover lifts it into a proof obligation at every call site, so the
   agent program fails closed if it passes (say) a negative volume.
   Write the predicate as a lambda in the supported Python subset; a named
   predicate function is refused by the prover. Chained comparisons such as
   `0 < x <= 200` are supported here, unlike in relation lambdas below.

3. **The body can do real work, or be a mock `pass`.** The prover
   never executes it under `clauz3 prove`; it runs only under
   `clauz3 run` after approval. Stdlib layers ship real bodies; example
   / fixture layers ship mocks.

**Optional second layer: `@deal.has(...)`.** deal's markers (`"io"`,
`"network"`, `"read"`, `"write"`, `"syscall"`, `"stdout"`, ...) are deal's
own vocabulary, and declaring them buys two things clauz3 does not do: deal
patches `socket`/`stdout`/`stderr` per call, so an undeclared side effect
raises rather than happens, and `clauz3 lint` (deal's linter) checks the
markers against what the body actually does. A layer whose effects do real
IO should declare them and run the linter in its `tests/Justfile`; a mock
layer can omit the markers. They play no part in what a contract can say.

Run `clauz3 lint` either way: its remaining rules are about contracts, not
markers, and they read `@clauz3.requires` / `@clauz3.ensures` too — a body
that contradicts its own postcondition is reported whether or not the layer
declares a marker.

## Effects that return data

Trusted effects can return data; they are not limited to `None`. The prover
models a symbolic result from the return annotation and narrows it using
`@clauz3.ensures`. It does not execute the effect body to discover what the API
returns. Use typed records (`clauz3.Row`, including generic `list[RowT]` queries)
and explicit size postconditions when callers need bounded iteration. The
trusted implementation must validate provider data and enforce its stated bound.
Row fields currently support `str`, `int`, and `bool`, not arbitrary SDK types.
Read [Returning typed data](references/returned-data.md) for complete signatures,
a caller, and the limits of postconditions and symbolic iteration.

## contracts.py: the predicate vocabulary

Contracts are `@contract`-decorated builder functions that return
`ContractSpec` values composed from a relation algebra over the
recorded effect facts.

```python
from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

from tools.email.trusted.effects import send_email

Email = effect(send_email)


@contract(description="No restrictions are imposed on email effects.")
def no_guarantees() -> ContractSpec:
    """Impose no email restrictions; other guarantees still apply."""
    return core_no_guarantees()


@contract(description="Email recipients must be in {addresses}.")
def only(addresses: list[str]) -> ContractSpec:
    """Guarantee every email recipient is in `addresses`."""
    return Email.all(lambda e: e.addr in addresses)


@contract(description="At most {count} email calls occur per run.")
def at_most(count: int) -> ContractSpec:
    """Limit the number of send_email calls on every reachable execution path."""
    return Email.count() <= count


@contract(description="Each recipient appears in at most one email call per run.")
def unique_recipients() -> ContractSpec:
    """Compare exact recipient strings within this run, not across conversations."""
    return Email.distinct(lambda e: e.addr)


@contract(description="No email body contains the literal text {substring}.")
def must_not_contain(substring: str) -> ContractSpec:
    """Exclude this case-sensitive literal; do not claim general secret detection."""
    return Email.all(lambda e: substring not in e.msg)
```

For a layer with several effects, import the functions the relation covers.

### Public documentation

Every public effect and contract needs a concise first docstring line and
fuller scope documentation. Tool discovery shows the docstring; generated
skills use its first line. Approval cards show the resolved library-qualified
call with named arguments, its `description` template (or first docstring
paragraph), and the full Python symbol. **Source and documentation** expands
the original agent expression, verified source snapshot, and full docstring.
Identity is resolved independently of templates; agent import aliases cannot
rename the displayed contract. Historical approvals retain their snapshots.
Keep this precise contract documentation separate from the manifest's human
overview. Keep literal placeholders in the template aligned with the function
parameters; test rendered descriptions with representative argument values.

For example, these declarations could belong to a filesystem layer's
`contracts.py`:

```python
from clauz3.spec import effect
from tools.filesystem.trusted.effects import append_file, write_file

WriteFile = effect(write_file)                # one effect
WriteText = effect(write_file, append_file)   # both text-writing effects
Any = effect()                                # every trusted call
```

When several tools act on the same resource, declare the events each performs
with `@performs` and state contracts over the events instead, so a new tool is
covered without changing the contract. Another library can import
`tools.filesystem.trusted.events.Write` and declare it with `@performs`; the
filesystem contracts then cover that library's declared writes too. Declare
`stdlib:filesystem` as a dependency and select both trusted roots. Import the
actual event class, rather than redefining one with the same name. See
[Sharing filesystem events](references/shared-filesystem-events.md) for a
bioinformatics interface, exact-file versus directory scope, and scratch files.

### Relation primitives

| Form | Meaning |
|---|---|
| `R.all(lambda e: pred(e))` | universal over reachable facts |
| `R.where(lambda e: pred).all(...)` | filtered universal |
| `R.empty()` / `R.where(...).empty()` | no fact satisfies / no fact matches |
| `R.count() <= n` (or `<`, `>=`, `>`) | bounded cardinality |
| `R.sum(lambda e: e.field) <= n` | bounded numeric aggregate |
| `R.distinct(lambda e: key(e))` | no two facts share the same key |
| `R1.shares_value(R2, lambda e: key)` | existential join between two filtered relations |

The lambda inside `.all` / `.where` is a *relation lambda* — a small
expression subset that compiles to Z3 constraints. It supports row
attributes (`e.addr`), comparisons (`==`, `!=`, `<`, `<=`, etc.),
boolean ops (`and`, `or`, `not`), `in` / `not in`, `len(...)`,
`str.startswith`/`str.endswith`, and a handful of other primitives.
**It does not yet support chained comparisons** (write `a < b and b < c`,
not `a < b < c`). It does *not* support arbitrary function calls;
attempting `e.addr.lower()` will raise `UnsupportedError`.

### Trust boundary

**The body runs as trusted Python; the lambda is compiled.** The helper body
runs as ordinary Python at prove time, its behavior is not proved, and the
`ContractSpec` it *returns* is what gets solved. The relation lambda
is never called: it is parsed and compiled into Z3 constraints, and
fails closed on anything outside the subset. So keep every helper
body to a one-line spec builder. A body that reads `os.environ`,
branches on anything but its arguments, or touches the file system
can silently return `no_guarantees()`, removing the intended restriction. Review `contracts.py` the way you review `effects.py`. Neither trusted helper
bodies nor event declarations are proved against their implementation.

### Naming conventions

For filesystem scopes, the bundled library distinguishes exact file lists
(`only_reads_files`, `only_writes_files`, accepting `list[FilePath]`) from
directory boundaries (`only_reads_files_under`, `only_writes_files_under`,
accepting `DirectoryPath`). Do not describe a literal-prefix helper as an
exact-file constraint. Account for recursive effects and implicit parent
creation when promising exact-path writes; document these limits explicitly.

Match the existing stdlib / examples pattern:

- `only_X(allowlist)` — universal over a field
- `never_X(prefix_or_list)` — universal negation
- `Xs_at_most(n)` — count cap
- `must_contain(s)` / `must_not_contain(s)` — content guard
- `no_X()` — explicit emptiness
- `no_guarantees()` — explicit null contract

## Rate limits and other runtime obligations

Contracts can cap calls per program, such as `effect(fetch_record).count() <= 10`.
They cannot express “three requests per second”: the proof has no wall clock,
sleep duration, or global request history. A fluent tracks modeled state within
one run; it does not supply a clock or a cross-process rate limiter.

Enforce provider timing policies in the trusted effect implementation (or its
shared client). Consult the provider's current rules rather than assuming one
limit applies to every account/API key. Throttle **every physical request**,
including pagination, retries, and redirects; honor retry guidance and bound
attempts. A single effect call may contain several HTTP requests, so label a
call-count contract as an adapter-call limit unless the implementation makes
and enforces a stronger mapping.

A module-level limiter only coordinates that process. If the provider quota is
shared across workers, conversations, API keys, or hosts, use coordination with
that scope or document the narrower operational guarantee. Avoid unlimited
waits/retries. Test scheduling with a fake monotonic clock, mocked waits, and a
fake transport; do not sleep or contact a provider to prove a rate-limit claim.
Record setup requirements and runtime throttling behavior in the manifest and
effect docstring. A successful proof does not establish provider rate compliance.

## Fluents: stateful (final-state) contracts

When the trusted layer needs to talk about *order* — e.g. "the door is
locked at the end" — relations alone are insufficient (they're a
multiset over facts, with no order). Add a fluent:

```python
import clauz3
from clauz3.fluent import effect, fluent

DoorLocked = fluent("door_locked", key=str, value=bool, initial=True)


@clauz3.trusted
@effect(lambda door: DoorLocked.set(door, False))
def unlock_door(door: str) -> None: ...


@clauz3.trusted
@effect(lambda door: DoorLocked.set(door, True))
def lock_door(door: str) -> None: ...
```

Then in `contracts.py`:

```python
from .effects import DoorLocked


@contract
def all_doors_locked_at_end() -> ContractSpec:
    return DoorLocked.final.all(lambda d: d.value == True)


@contract
def door_locked_at_end(door: str) -> ContractSpec:
    return DoorLocked.final[door] == True
```

Fluents encode a Z3 array threaded through the recorded fact sequence
in program order. Each `@effect` lambda is applied as a guarded store.
Contracts query the *final* valuation after all reachable calls have
been folded in — order matters (`unlock; lock` is different from
`lock; unlock`).

Fluent caveats in v1:

- `@effect`-bearing trusted calls cannot appear inside loops.
- Keys and values must be `bool`, `int`, or `str`.
- Queries are over `.final` only (no intermediate / quantified-over-time).

## Packaging as an installable library

Add a `tests/Justfile` so `clauz3 test gh:org/repo` can run your suite:

```just
clauz3 := env_var_or_default("CLAUZ3", "uv run clauz3")

trusted := "../tools/<domain>/trusted"
effects := trusted + "/effects.py"
prove := clauz3 + " prove --trusted-root " + trusted + " --import-root .."

test: lint cases

lint:
    {{clauz3}} lint {{effects}}

cases: only-X-pass only-X-fail no-X-pass ...

only-X-pass:
    {{prove}} cases/only_X_pass.py

only-X-fail:
    if {{prove}} cases/only_X_fail.py; then exit 1; fi

# ... etc
```

The `CLAUZ3` env var indirection lets the suite run both
from a source checkout (`uv run`) and from an installed wheel under
`clauz3 test`.

Every contract should have *both* a `_pass.py` and a `_fail.py` case
under `tests/cases/`. The `_fail.py` form proves the contract correctly
rejects programs that violate it — without it you're not testing that
the guarantee is enforced, only that there's no error path.

## Common pitfalls

- **Defaults are not in facts.** A trusted-function keyword default
  like `dry_run: bool = True` is *not* reflected into the recorded
  effect fact. A contract querying `e.dry_run` against a call that
  relied on the default fails closed. This is the intentional
  pattern behind `dry_run_only()` in `stdlib:google_workspace`; if you
  want it, force agents to pass the kwarg explicitly. If you want
  defaults reflected, that needs a clauz3-core change.

- **Forgetting `@clauz3.requires` means the prover infers
  unconstrained inputs.** A symbolic `int` argument can be negative
  or huge unless a precondition bounds it. Bound where possible.

- **Incomplete effect groups.** A relation covers only the functions it
  names. When adding an effect, update each group that should constrain it;
  deal's `read` or `write` markers do not add it to another layer's policies.

- **Relation lambda gotchas.** `e.addr.lower()` raises
  `UnsupportedError`. Chained comparisons (`0 < x <= 200`) inside a
  relation lambda raise `UnsupportedError`. Stick to the supported
  subset; if you need more, the contract is probably trying to do too
  much and should be split.

- **A quiet no-match is a proven no-op.** If an effect resolves an
  identifier (room, device id, path, table, address) against external
  state, it must raise when nothing matches. A proof over
  `only_rooms(["Dining Room"])` says nothing about whether the bridge
  has such a room; wrapping a library whose `get_*_by_name` logs and
  returns `None` turns the proof into a guaranteed no-op. Test the missing-identifier case;
  distinguish it from an explicitly documented empty search result.

## Reference layers to read

For working examples in increasing order of complexity:

- `examples/email/` in the `clauz3` repo — minimal mock, single effect.
- `src/clauz3/stdlib/filesystem/` — real file effects whose contracts are
  stated over events, so one path policy covers every tool.
- `examples/home-automation/` — relational + fluent contracts in one
  layer.
- `normalform-ai/clauz3-tools-autolabs` — production LIMS layer with
  state via fluents.
- `src/clauz3/stdlib/google_workspace/` — Gmail drafts and sends via gog, with
  `dry_run_only()` and subprocess routing tests. This repository owns Google
  Workspace integrations; do not develop them in a separate assistant repo.
  The current email implementation supports gog, not the separate gws CLI.

## When done

- All `_pass.py` cases prove cleanly.
- All `_fail.py` cases are correctly rejected (non-zero exit from
  `clauz3 prove`).
- `clauz3 lint tools/<domain>/trusted/effects.py` is clean.
- Every identifier parameter either raises on no-match at run time
  (covered by a runtime smoke test), or its quiet semantics are
  documented so guarantees over it read as intent-only.
- `README.md` shows the install command and the headline contracts.
- `tools/<domain>/clauz3.yaml` validates against the manifest schema and describes
  human-facing capabilities, runtime prerequisites, and any user-managed setup.
- An optional root package manifest contains only shared setup; installation
  produces no missing-manifest warnings, and generated snapshots are not authored
  by hand.
- Return annotations and postconditions match actual provider parsing and bounds;
  test malformed, oversized, and empty responses. Shared events account for all
  relevant paths, including subprocess scratch files and sidecars. Provider
  rate limits are enforced at runtime with the documented coordination scope.
- Public effects and contracts have docstrings; contract description templates
  accurately state their scope without making stronger claims than the code.
- `AGENTS.md` documents the consumer-side discipline (which contracts
  to combine, how to pass kwargs, sandbox conventions if any).
- Add only the source location to `src/clauz3/library_registry.yaml` once the
  library is intended for shared discovery. Installed manifests work without
  a registry entry; no second copy of the description is required.
