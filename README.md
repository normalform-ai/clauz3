<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/normalform-ai/clauz3/main/docs/assets/logo-lockup-dark.svg">
    <img src="https://raw.githubusercontent.com/normalform-ai/clauz3/main/docs/assets/logo-lockup.svg" alt="ClauZ3 — Static contracts for agent-authored Python" width="420">
  </picture>
</p>

# clauz3

Static contracts for agent-authored Python.

`clauz3` experiments with a different permission surface for agents: the
agent writes a small Python program, attaches a contract describing its trusted
side effects, and a static prover checks the contract before anything runs.

The contract is the thing the user should be asked to accept. The program is
still available for inspection, but a user should not have to read every branch
to answer questions like:

- Will this email anyone other than Bob?
- Will this email the same person twice?
- Will this withdraw more than $5 total?
- Will this write outside a sandbox?

## In one picture

A run, end to end. The user asks for a bill to be paid (capped); the agent
writes a small program that looks up the outstanding balance and pays
`min(balance, $500)`; ClauZ3 proves the spend stays under the cap on *every*
branch and that only the named account is touched; the user approves the
*guarantees*, not the code; only then does the runtime execute it.

<p align="center">
  <img src="docs/assets/figure-sequence.png"
       alt="UML sequence diagram of a ClauZ3 run. Lifelines: User, Agent, ClauZ3 gate, Python runtime. The User asks the Agent to pay a card bill capped at $500. The Agent submits a program plus guarantees to the ClauZ3 gate as a single tool call. The prover proves spending stays under $500 in both branches and that only the card account is paid, then presents only the two guarantees to the User, who approves. ClauZ3 invokes the Python runtime to execute the program; the result returns up through the agent, which summarizes success in plain English."
       width="900">
</p>

<sub>Editable vector source: <a href="docs/assets/figure-sequence.svg">docs/assets/figure-sequence.svg</a>.</sub>

## Current Shape

Projects are split into three layers:

- trusted roots such as `tools/email/trusted/`: small audited modules
  containing both side-effecting functions and reusable domain contracts. These
  are the environment contract: the prover trusts their signatures,
  `@deal.has(...)` markers, `@deal.pre(...)` preconditions, and `@contract`
  helper definitions.
- agent-authored code: ordinary Python that imports trusted functions and adds
  `@clauz3.guarantee(...)` decorators to the function being proved.

Trusted calls bottom out into symbolic effect facts. For example:

```python
@deal.pre(lambda addr, msg: "@" in addr)
@deal.has("trusted")
def send_email(addr: str, msg: str) -> None:
    ...
```

A call to `send_email("bob@example.com", "hi")` creates a fact whose relation
name is `send_email`, whose fields are `addr` and `msg`, and whose condition is
the branch condition under which the call is reachable.

Domain logic can query those generated facts:

```python
from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees


Email = effect("send_email")


@contract
def only(addresses: list[str]) -> ContractSpec:
    return Email.all(lambda e: e.addr in addresses)


@contract
def no_guarantees() -> ContractSpec:
    return core_no_guarantees()


@contract
def unique_recipients() -> ContractSpec:
    return Email.distinct(lambda e: e.addr)
```

Agent code then states guarantees in terms of that vocabulary:

```python
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["bob@example.com"]))
@clauz3.guarantee(emails.unique_recipients())
def main() -> None:
    send_email("bob@example.com", "hi")
```

Run the prover:

```bash
cd examples/email
uv run clauz3 prove \
  --trusted-root tools/email/trusted \
  cases/only_bob_pass.py
```

For programs that combine domains, either repeat `--trusted-root` or pass
several roots with `--trusted-roots`:

```bash
uv run clauz3 prove plan.py \
  --trusted-roots tools/email/trusted tools/db/trusted
```

## Installing a trusted layer

To start a new project from an existing one, copy its trusted `tools/` layer
with `clauz3 install`. It accepts a local path, a `gh:org/repo` shorthand, or
a full git URL, and copies each `tools/<domain>/` into the current directory:

```bash
# Local path
clauz3 install /path/to/repo/examples/email

# GitHub shorthand (clones into ~/.cache/clauz3/sources/<sha>/)
clauz3 install gh:normalform-ai/clauz3-tools-autolabs

# Pin to a tag, branch, or sha for reproducibility
clauz3 install gh:normalform-ai/clauz3-tools-autolabs@v0.3.1

# Full URL (HTTPS or SSH)
clauz3 install git@github.com:normalform-ai/clauz3-tools-assistant.git
```

Pass `--into <dir>` to target a different destination, `--force` to overwrite an
existing layer, and `--skills` to also generate
`agents/skills/<domain>/SKILL.md` describing the installed effects and
contracts:

```bash
clauz3 install gh:normalform-ai/clauz3-tools-autolabs --into my-project --skills
```

Authentication for git remotes uses your existing git configuration (SSH key
or credential helper); `clauz3` does not manage credentials itself. The remote
cache is keyed by the resolved commit sha; pinning to `@<sha>` hits a stable
entry forever, while ref-pinned or HEAD installs re-resolve on each call and
get a fresh cache entry whenever upstream moves. Override the cache location
with `CLAUZ3_CACHE`.

The install itself is a filesystem copy. Signing — so a user can verify the
installed layer matches what was attested upstream — is tracked in
[issue #47](https://github.com/cmungall/agent-deal/issues/47).

## Standard library tools

`clauz3` ships a small set of **real (non-mock) tools** under
[`src/clauz3/stdlib`](src/clauz3/stdlib). After `pip install clauz3`, install
one into the current repo with the `stdlib:` scheme:

```bash
clauz3 install stdlib:filesystem
clauz3 install stdlib:grep
```

The bundled tools are located with `importlib.resources`, so this works the
same from a source checkout or an installed wheel. (A bare `clauz3 install
filesystem` also works as a shorthand.)

| Tool | Effects | Headline contracts |
| --- | --- | --- |
| [`filesystem`](src/clauz3/stdlib/filesystem) | `read_file`, `write_file` | `read_only`, `only_write_under`, `never_read_under`, `writes_at_most` |
| [`grep`](src/clauz3/stdlib/grep) | `grep` (imports `filesystem`) | `only_read_under`, `never_read_under`, `searches_at_most`, `only_pattern` |

These differ from the `examples/` layers in two ways: their effect bodies do
real work (so the prover skips them but `clauz3 run` executes them), and their
proof cases live under `tests/cases/` with a `tests/Justfile` rather than at the
top level. `just stdlib` proves them all, or run one library's suite directly
with `clauz3 test stdlib:filesystem` (it invokes the library's `tests/Justfile`
with [`just`](https://just.systems)).

## Examples

The repo currently has five worked examples:

| Example | Contracts | What it demonstrates |
| --- | --- | --- |
| [`examples/email`](examples/email) | `emails.only`, `emails.none`, `emails.no_guarantees`, `emails.unique_recipients`, `emails.content_length_at_most` | allowlists, explicit absence of guarantees, absence of effects, pairwise uniqueness, string length bounds |
| [`examples/bank`](examples/bank) | `bank.max_spend`, `bank.only_account` | numeric aggregation, field equality, and fixed-bound loop unrolling over trusted calls |
| [`examples/email-from-db`](examples/email-from-db) | `emails.addresses_from`, `db.only_table`, `db.only_where` | for-loops over trusted query returns, column-binding constraints |
| [`examples/text`](examples/text) | `text.length_between`, `text.must_not_contain`, `text.no_regex_metacharacters`, `text.only_edit_under`, `text.edit_length_at_most` | string-length bounds, required/banned substrings, regex-metacharacter safety before sending, and file-edit path/size policies |
| [`examples/http`](examples/http) | `http.host_only`, `http.no_posts` | a shared `@deal.has` marker spanning two trusted calls, url-prefix matching, and method-specific absence of effects |

All examples use generic effect relations inferred from trusted function
signatures. There is no email-specific, bank-specific, or database-specific logic in the core.

## Status

This is experimental. The current prover vendors and extends `deal-solver`.
Implemented pieces include:

- `clauz3.guarantee(...)`, a no-op runtime decorator consumed by the prover.
- trusted-call fact recording at `@deal.has(...)` boundaries.
- `clauz3.spec.effect(...)` for relation-style queries over trusted facts.
- relation primitives: `no_guarantees`, `all`, `empty`, `where`, `count`,
  `distinct`, numeric `sum`, and `shares_value`.
- stateful **fluents** (`clauz3.fluent`): trusted functions declare
  successor-state axioms with `@effect(...)`, and contracts query the final
  valuation with `Fluent.final.all(...)` / `Fluent.final[key] == value`. This
  expresses order- and post-state-sensitive policies such as "every door is
  locked at the end" that the multiset relation language cannot. See
  [docs/reference/fluents.md](docs/reference/fluents.md).
- `clauz3 prove` for proving examples from the CLI.
- `clauz3 install` for copying a trusted `tools/` layer from a local path or a
  bundled stdlib tool (`stdlib:filesystem`, `stdlib:grep`), optionally
  generating `agents/skills/<domain>/SKILL.md` stubs.
- `clauz3 test` for running a library's bundled `tests/Justfile` with `just`,
  resolving the source the same way as `install`.
- `clauz3 config` for writing this repo's default Claude Code permissions
  (read-only tools plus the `clauz3` CLI) to `.claude/settings.json`. Idempotent
  and the configuration counterpart to `install`.
- `clauz3 run` for proving a complete inline program, submitting an approval
  request to an externally configured approval service, and executing `main`
  only after an approval receipt is returned.
- `clauz3 approval-service` for starting a simple localhost FastAPI approval
  service with REST endpoints and a browser UI for user decisions. With
  `--policy`, a policy admin's rules can auto-approve or auto-reject a request
  by asking the prover whether the program *entails* the rule's contracts,
  falling back to a human otherwise. `clauz3 policy-check` dry-runs a policy
  against a program. See
  [docs/todos/approval-policies.md](docs/todos/approval-policies.md).
- `clauz3 mock-approval-service` for config-driven tests and local demos.
- For-loops over `list[Row]`-returning trusted calls, with column-binding
  contracts via `UserRow.email` markers. See
  [docs/explanation/symbolic-iteration.md](docs/explanation/symbolic-iteration.md).
- Domain coverage policies declared in a trusted-root `policy.py`: contracts a
  domain `recommended`s are flagged in the approval UI when a used domain is
  under-constrained, and contracts it marks `required` are conjoined into the
  proof and rejected if unmet. See
  [docs/reference/coverage-policies.md](docs/reference/coverage-policies.md).

Important limitations:

- Contract helper functions are still executed as builder functions. Their
  lambda bodies are parsed as a small expression subset, but the helper body
  itself is not yet fully AST-validated.
- Runtime receipt enforcement in trusted functions is not implemented yet; the
  first `run` slice requires a receipt before execution and exposes it to the
  process as `CLAUZ3_APPROVAL_RECEIPT`.
- `clauz3 run` includes source checks and reduced builtins, but it is not a
  hardened Python or OS sandbox. The agent harness must still restrict execution
  to the `clauz3` command path.
- The relation language is intentionally small. Richer string predicates,
  richer aggregates, Datalog-style rules, and deterministic
  natural language summaries are future work.

More detail:

- [FAQ and terminology](docs/explanation/faq.md)
- [Concepts](docs/explanation/concepts.md)
- [Background and related work](docs/explanation/background.md)
- [Effect specs](docs/reference/effect-specs.md)
- [Coverage policies](docs/reference/coverage-policies.md)
- [Symbolic iteration](docs/explanation/symbolic-iteration.md)
- [ClauZ3 Python subset](docs/reference/python-subset.md)
- [Approval service](docs/how-to/approval-service.md)
- [User approval dialog design](docs/todos/user-approval-dialog.md)
- [Guardians synergies](docs/todos/guardians-synergies.md)
- [Ideas](docs/todos/ideas.md)

## Development

```bash
uv sync --dev
just test
```

## Built on

<p>
  <a href="https://deal.readthedocs.io/">
    <img src="https://raw.githubusercontent.com/life4/deal/master/logo.png"
         alt="deal logo" height="48" align="middle">
  </a>
  &nbsp;&nbsp;
  <a href="https://github.com/Z3Prover/z3">
    <img src="https://github.com/Z3Prover.png" alt="Z3 logo" height="48"
         align="middle" style="border-radius: 8px;">
  </a>
</p>

ClauZ3 layers on two upstream projects and would not exist without them:

- **[deal](https://deal.readthedocs.io/)** — the Python runtime contract
  engine whose `@deal.pre`, `@deal.post`, and `@deal.has` decorators define
  the trusted-layer vocabulary. ClauZ3 reads the same decorators and
  discharges them statically; in runtime-only mode, deal enforces them at
  execution. See
  [Static proof vs runtime](docs/explanation/concepts.md#static-proof-vs-runtime)
  for how the two layers relate.
- **[Z3](https://github.com/Z3Prover/z3)** — Microsoft Research's SMT solver,
  the back-end the prover compiles every guarantee into. The vendored
  `deal-solver` machinery is what bridges Python AST and Z3 constraints.
