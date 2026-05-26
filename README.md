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
with `clauz3 install`. It takes a local path to a project (or directly to a
`tools/` folder) and copies each `tools/<domain>/` into the current directory:

```bash
clauz3 install /path/to/repo/examples/email
```

Pass `--into <dir>` to target a different destination, `--force` to overwrite an
existing layer, and `--skills` to also generate
`agents/skills/<domain>/SKILL.md` describing the installed effects and
contracts:

```bash
clauz3 install /path/to/repo/examples/email --into my-project --skills
```

For now this is a trivial filesystem copy. Future work will add signing so a
user has guarantees that the installed layer is untouched.

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
top level. `just stdlib` proves them all.

## Examples

The repo currently has three worked examples:

| Example | Contracts | What it demonstrates |
| --- | --- | --- |
| [`examples/email`](examples/email) | `emails.only`, `emails.none`, `emails.no_guarantees`, `emails.unique_recipients`, `emails.content_length_at_most` | allowlists, explicit absence of guarantees, absence of effects, pairwise uniqueness, string length bounds |
| [`examples/bank`](examples/bank) | `bank.max_spend`, `bank.only_account` | numeric aggregation, field equality, and fixed-bound loop unrolling over trusted calls |
| [`examples/email-from-db`](examples/email-from-db) | `emails.addresses_from`, `db.only_table`, `db.only_where` | for-loops over trusted query returns, column-binding constraints |

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
- `clauz3 prove` for proving examples from the CLI.
- `clauz3 install` for copying a trusted `tools/` layer from a local path or a
  bundled stdlib tool (`stdlib:filesystem`, `stdlib:grep`), optionally
  generating `agents/skills/<domain>/SKILL.md` stubs.
- `clauz3 run` for proving a complete inline program, submitting an approval
  request to an externally configured approval service, and executing `main`
  only after an approval receipt is returned.
- `clauz3 approval-service` for starting a simple localhost FastAPI approval
  service with REST endpoints and a browser UI for user decisions.
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
