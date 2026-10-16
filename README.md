<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/normalform-ai/clauz3/main/docs/assets/logo-lockup-dark.svg">
    <img src="https://raw.githubusercontent.com/normalform-ai/clauz3/main/docs/assets/logo-lockup.svg" alt="ClauZ3 — Static contracts for agent-authored Python" width="420">
  </picture>
</p>

# clauz3

[![PyPI](https://img.shields.io/pypi/v/clauz3.svg)](https://pypi.org/project/clauz3/)
[![Python versions](https://img.shields.io/pypi/pyversions/clauz3.svg)](https://pypi.org/project/clauz3/)
[![Docs](https://img.shields.io/badge/docs-normalform--ai.github.io%2Fclauz3-blue)](https://normalform-ai.github.io/clauz3/)

Static contracts for agent-authored Python.

Today an agent that wants to act in the world either asks you to click
"Allow" on every tool call, or gets a blanket grant like "may use email".
`clauz3` asks once, and asks a better question:

> This program will email only `bob@example.com`, at most once. **Approve?**

The agent writes a small Python program and attaches guarantees about its side
effects. A Z3-backed prover checks those guarantees against *every* branch of
the program before anything runs. You approve the guarantees; the program runs
only after that, all or nothing.

The guarantees are the thing the user is asked to accept. The program is still
available for inspection, but a user should not have to read every branch to
answer questions like:

- Will this email anyone other than Bob?
- Will this email the same person twice?
- Will this spend more than $500 in total?
- Will this write outside a sandbox?

Agents increasingly get work done by writing programs that call tools rather
than one tool call at a time. A program has structure a prover can reason
about; `clauz3` is built for that input. For how this compares with permission
prompts, sandboxes, guardrails, and runtime policy monitors, see
[How ClauZ3 compares](docs/explanation/comparison.md).

## In one picture

Want to try it first? The browser-first quickstarts walk through
[mock email](docs/quickstarts/mock-email.md) and
[real file work](docs/quickstarts/real-files.md), with prompts, approval checks,
expected results, and terminal alternatives.

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

For chat, proof progress, and approvals in one browser workspace:

```bash
npm install -g @agentclientprotocol/claude-agent-acp@0.89.1
clauz3 ui claude
```

The local workspace uses ACP to host Claude and the existing Clauz3 MCP tools
to prove and execute actions. It includes streamed conversation, inline
approvals, proof diagnostics, a file browser, persistent conversation history,
approval profiles, and library management. In a fresh folder, **Libraries**
opens automatically: review an installation, select tools, and start a new
conversation. Claude must already be authenticated. See the
[UI reference](docs/reference/cli.md#clauz3-ui) for setup and session lifetime.

For an agent session, run:

```bash
clauz3 start claude
```

This starts a localhost approval service, opens its private approval UI, and
launches Claude Code in the current folder with program submission,
installed-tool discovery, and read-only library search available. In an empty
project it offers a library chooser with a File work bundle preselected. It
provisions signing keys and separate
submission/approver tokens automatically, and stops the service when the agent
exits. Claude Code must already be installed and signed in. See the
[quickstart](docs/quickstart.md) and [start reference](docs/reference/cli.md#clauz3-start).

Both the UI and CLI support `claude`, `codex`, `agy` (Antigravity), `opencode`,
and `pi` on macOS and Linux. Claude and Pi support restricted sessions; Codex,
Antigravity, and OpenCode require explicit `--unrestricted` opt-in. See
[agent setup](docs/reference/cli.md#clauz3-ui) for optional runtimes and
authentication. In `clauz3 start claude`, `--unrestricted` enables native Claude
tools for debugging, with Claude's normal permission prompts; those tools can
bypass the clauz3 approval flow. Claude's browser integration remains restricted
and rejects `--unrestricted`.

Projects are split into two layers:

- trusted roots such as `tools/email/trusted/`: small audited modules
  containing both side-effecting functions and reusable domain contracts. These
  are the environment contract: the prover trusts their signatures,
  `@clauz3.trusted` effect declarations, `@clauz3.requires(...)`
  preconditions, and `@contract` helper definitions.
- agent-authored code: ordinary Python that imports trusted functions and adds
  `@clauz3.guarantee(...)` decorators to the function being proved.

Trusted calls bottom out into symbolic effect facts. For example:

```python
import clauz3


@clauz3.requires(lambda addr, msg: "@" in addr)
@clauz3.trusted
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

from tools.email.trusted.effects import send_email

Email = effect(send_email)


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

A guarantee must be a call to a registered `@contract` helper with literal
arguments — see [the guarantee
grammar](docs/reference/python-subset.md#the-guarantee-grammar). Agent code
cannot supply its own predicate, so a guarantee is always a claim the trusted
layer defined and the user can look up.

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

Browse bundled and curated external libraries with `clauz3 libraries`, or search
with `clauz3 libraries email`. Results show dependencies and install commands;
listing never downloads or executes library code.

To start a new project from an existing one, copy its trusted `tools/` layer
with `clauz3 install`. It accepts a local path, a `gh:org/repo` shorthand, or
a full git URL, and copies each `tools/<domain>/` into the current project's
`.clauz3/tools/`, keeping the project's top level clear:

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
`.clauz3/skills/<domain>/SKILL.md` describing the installed effects and
contracts:

```bash
clauz3 install gh:normalform-ai/clauz3-tools-autolabs --into my-project --skills
```

Use `-g` / `--global` for a reusable user-wide installation, then select it into
individual projects:

```bash
clauz3 install -g stdlib:grep
clauz3 install global:grep
```

The second command copies `grep` and its `filesystem` dependency into this
project. Global installations are not automatically trusted by projects;
updating the global copy leaves existing project copies unchanged. The store
defaults to `~/.local/share/clauz3` (honoring `XDG_DATA_HOME` and `CLAUZ3_HOME`).

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
| [`filesystem`](src/clauz3/stdlib/filesystem) | `read_file`, `read_lines`, `write_file` | `read_only`, `only_write_under`, `never_read_under`, `writes_at_most` |
| [`grep`](src/clauz3/stdlib/grep) | `grep` (imports `filesystem`) | `only_read_under`, `never_read_under`, `searches_at_most`, `only_pattern` |

These differ from the `examples/` layers in two ways: their effect bodies do
real work (so the prover skips them but `clauz3 run` executes them), and their
proof cases live under `tests/cases/` with a `tests/Justfile` rather than at the
top level. `just stdlib` proves them all, or run one library's suite directly
with `clauz3 test stdlib:filesystem` (it invokes the library's `tests/Justfile`
with [`just`](https://just.systems)).

## Examples

The repo has ten worked examples:

| Example | Contracts | What it demonstrates |
| --- | --- | --- |
| [`examples/email`](examples/email) | `emails.only`, `emails.none`, `emails.no_guarantees`, `emails.unique_recipients`, `emails.content_length_at_most` | allowlists, explicit absence of guarantees, absence of effects, pairwise uniqueness, string length bounds |
| [`examples/bank`](examples/bank) | `bank.max_spend`, `bank.only_account` | numeric aggregation, field equality, and fixed-bound loop unrolling over trusted calls |
| [`examples/email-from-db`](examples/email-from-db) | `emails.addresses_from`, `db.only_table`, `db.only_where` | for-loops over trusted query returns, column-binding constraints |
| [`examples/text`](examples/text) | `text.length_between`, `text.must_not_contain`, `text.no_regex_metacharacters`, `text.only_edit_under`, `text.edit_length_at_most` | string-length bounds, required/banned substrings, regex-metacharacter safety before sending, and file-edit path/size policies |
| [`examples/http`](examples/http) | `http.host_only`, `http.no_posts` | an explicit relation grouping `http_get` and `http_post`, URL-prefix matching, and method-specific absence of effects |
| [`examples/trading`](examples/trading) | `trading.approvals_only_desk`, `trading.approvals_only_where`, `trading.stock_from`, `trading.shares_from`, `trading.orders_at_most` | a port of [Dogwood](docs/explanation/background.md#dogwood)'s temporal approval-gate policy: an order's stock and quantity must each come from *some* approval row queried with an `approved` filter (they are not yet pinned to the *same* row — see the walk-through) — the same gate expressed as provenance rather than as a window over event history ([walk-through](docs/examples/trading.md)) |
| [`examples/gh-triage`](examples/gh-triage) | `gh.comments_only_where`, `gh.urls_from` | source- and data-flow *provenance*: the comment-reading and archive-download tools stay fully general, but contracts pin every consumed comment to a members-only query and every download URL to a release asset posted by a repo member — neutralizing the [10K-repo GitHub malware campaign](docs/how-to/malware-repo-defense.md) that baits AI agents into downloading attacker payloads |
| [`examples/lims`](examples/lims) | `lims.only_plate`, `lims.total_pipette_volume_at_most`, `lims.only_instruments`, `lims.total_runtime_at_most`, `lims.no_hazard_sequence` | an autonomous-science analog of `bank`: numeric aggregation, allowlists, and substring guards over pipetting, instrument time, and oligo orders ([walk-through](docs/examples/lims.md)) |
| [`examples/home-automation`](examples/home-automation) | `home.temp_between`, `home.only_unlock_doors`, `home.all_doors_locked_at_end`, `home.grocery_budget` | everyday-stakes allowlists and budgets, plus [fluents](docs/reference/fluents.md) for end-state properties such as "every door is locked at the end" ([walk-through](docs/examples/home-automation.md)) |
| [`examples/cloud`](examples/cloud) | `cloud.cap_at_most`, `cloud.total_caps_at_most`, `cloud.never_remove_caps`, `cloud.all_services_capped_at_end` | [hard budget caps](https://simonwillison.net/2026/Oct/3/default-hard-budget-caps/) on agent deploys: per-service and total cap bounds, clamping a cap computed from an unbounded estimate, and a fluent for "no service left uncapped at the end" ([walk-through](docs/examples/cloud.md)) |

All examples use generic effect relations inferred from trusted function
signatures. There is no email-specific, bank-specific, or database-specific logic in the core.

Use `*_pass.py` for a proved case, `*_fail.py` for a refuted case, and
`*_skip.py` for an unsupported construct. Both failure and skip reject
execution, but only failure establishes a counterexample.
The bank, email, cloud, gh-triage, email-from-db, HTTP, and text examples pin exact
conclusions and obligation descriptions in pytest, with discovery checks that
require every case to have a table entry. Home automation, LIMS, and trading
lack complete conclusion tables; their example recipes check negative cases
only for rejection. `policy_*.py` files are approval-policy fixtures and are
excluded from the proof tables.

The email pair `aug_assign_skip.py` and `aug_assign_rewritten_fail.py` shows
why this distinction matters: `msg += ...` currently leaves the supported
subset, while `msg = msg + ...` lets the prover refute the same length bound.
The skip tests pin the prover and interpreter refusal reasons and check the
runner refuses before requesting approval. Adding augmented-assignment support
([#139](https://github.com/cmungall/agent-deal/pull/139)) should replace this
skip fixture with an ordinary refutation.

## Skills marketplace

This repo is also a Claude Code skills marketplace. The
`.claude-plugin/marketplace.json` ships three skills covering the three
clauz3 roles:

- **`clauz3`** — for an agent that needs to do real-world things in a
  project where `clauz3` is the only outlet.
- **`clauz3-authoring`** — for designing a new trusted layer.
- **`clauz3-exploring`** — for stress-testing agents that talk to `clauz3`.

Add the marketplace to Claude Code with:

```bash
claude plugin marketplace add normalform-ai/clauz3
```

Then install whichever plugin(s) match your role. The skills themselves
live in `skills/` and are also usable directly by other harnesses
(`--skill /path/to/skills/clauz3/SKILL.md` for Pi-style runners).

## Status

This is experimental. The current prover vendors and extends `deal-solver`.
Implemented pieces include:

- `clauz3 start claude` for launching a restricted agent and its approval
  service together, with automatic session credentials and cleanup.
- `clauz3.guarantee(...)`, a no-op runtime decorator consumed by the prover,
  restricted to calls of registered `@contract` helpers over literals.
- trusted-call fact recording at `@clauz3.trusted` boundaries (a legacy
  `@deal.has(...)` carrying markers still works).
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
  bundled stdlib tool (`stdlib:filesystem`, `stdlib:grep`) into the project's
  `.clauz3/tools/`, optionally generating `.clauz3/skills/<domain>/SKILL.md`
  stubs.
- `clauz3 test` for running a library's bundled `tests/Justfile` with `just`,
  resolving the source the same way as `install`.
- `clauz3 lint` for checking a trusted layer's optional
  [deal](docs/how-to/deal.md) markers against what its bodies actually do.
- `clauz3 config` for writing this repo's default Claude Code permissions
  (read-only tools plus the `clauz3` CLI) to `.claude/settings.json`. Idempotent
  and the configuration counterpart to `install`.
- `clauz3 run` for proving a complete inline program, submitting an approval
  request to an externally configured approval service, and executing `main`
  only after a **signed** approval receipt is verified. Verification is
  fail-closed: `run` refuses to execute unless `CLAUZ3_APPROVAL_VERIFY_KEY` is
  provisioned. Use `clauz3 approval-keygen` to generate the keypair (signing key
  for the service, verifying key for the runner); see
  [docs/how-to/approval-service.md](docs/how-to/approval-service.md).
- `clauz3 approval-keygen` for generating the Ed25519 approval-signing keypair.
- `clauz3 approval-service` for starting a simple localhost FastAPI approval
  service with REST endpoints and a browser UI for user decisions. With
  `--policy`, a policy admin's rules can auto-approve or auto-reject a request
  by asking the prover whether the program *entails* the rule's contracts,
  falling back to a human otherwise. `clauz3 policy-check` dry-runs a policy
  against a program. See
  [docs/todos/approval-policies.md](docs/todos/approval-policies.md).
- `clauz3 mock-approval-service` for config-driven tests and local demos.
- `clauz3.mcp.MCPClient` is a small helper for TLEs
  who want to wrap an existing untrusted MCP server in a trusted layer: a
  short `effects.py` shim forwards calls into the upstream server while
  carrying the usual `@clauz3.trusted` declarations and contracts that clauz3
  proves before any side effect runs. `start` includes a private, restricted
  MCP bridge; the standalone MCP service lives in a separate `clauz3-mcp`
  package and is added to a harness as `uvx clauz3-mcp`. See
  [docs/how-to/mcp.md](docs/how-to/mcp.md).
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

- [How ClauZ3 compares](docs/explanation/comparison.md)
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

To use a checkout as your everyday `clauz3` command, run this from the checkout:

```bash
just install-editable
```

This uses `uv tool install --editable .` to install a user-wide command in an
isolated tool environment. "Editable" means it loads Python source directly
from this checkout, so edits and `git pull` updates take effect on the next
launch. If `clauz3` is not on your shell's PATH, run `uv tool update-shell` and
open a new terminal. Remove any earlier `clauz3` shell alias or function so it
does not override the installed command.

Keep this checkout on `main` for everyday use. Exit running sessions before
updating, then launch from the folder the agent should work in:

```bash
git pull --ff-only                 # in the clauz3 checkout
just reinstall                    # when dependencies or command definitions change
cd /path/to/your/project
clauz3 start claude
```

`just reinstall` refreshes the tool and its dependencies while keeping the
editable link to the checkout where you run the recipe. Tool installation
resolves dependencies from `pyproject.toml`; it does not use the checkout's
`uv.lock`. For an isolated feature test using a separate checkout and its
project environment, run from your target project:

```bash
uv run --project /path/to/feature-checkout clauz3 start claude
```

Application code is grouped under `src/clauz3/sessions/` (agent launch and
lifecycle), `approvals/` (approval policy, receipts, service and UI), and
`libraries/` (discovery, installation and setup). The contract API remains at
`clauz3`; the prover, interpreter and vendored solver retain their existing
locations.

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
  engine `@clauz3.requires` / `@clauz3.ensures` are built on, so every
  condition a trusted layer states is also enforced at execution. A layer can
  use deal directly to bound what its own *bodies* may do; see
  [Extra runtime enforcement with deal](docs/how-to/deal.md). See
  [Static proof vs runtime](docs/explanation/concepts.md#static-proof-vs-runtime)
  for how the two layers relate.
- **[Z3](https://github.com/Z3Prover/z3)** — Microsoft Research's SMT solver,
  the back-end the prover compiles every guarantee into. The vendored
  `deal-solver` machinery is what bridges Python AST and Z3 constraints.
