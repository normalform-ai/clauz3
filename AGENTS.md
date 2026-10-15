# Repository Guidelines

## Project Structure & Module Organization

`src/clauz3/` contains the CLI, prover, contract-spec DSL, and vendored
`deal_solver` code under `_vendor/`. Tests live in `tests/`. Worked examples
live in `examples/<domain>/`: each has `cases/` for agent-authored proof
fixtures and `tools/<domain>/trusted/` for trusted effects and contracts.
Design notes are in `docs/` and `docs/todos/`. CI is defined in
`.github/workflows/main.yaml`.

Session launch, lifecycle, MCP bridge, and selected skill documents live in
`sessions/`; approval clients, authority, policy, receipts, service, and UI live
in `approvals/`; library discovery, installation, and setup live in `libraries/`.
`clauz3 install` copies libraries into a project's `.clauz3/tools/`, which is
added as an import root ahead of the project (`libraries/layout.py`); examples,
library sources and the stdlib keep the authored `tools/` layout.
Use these subpackages for new internal imports. The small top-level `approval*`
modules preserve existing embedding imports; `clauz3.libraries` re-exports its
discovery API. Keep the root `clauz3` contract API stable.

## Build, Test, and Development Commands

- `uv sync --dev`: install the project and development tools.
- `just test`: run pytest, ruff, mypy, the web UI node tests, and every
  example Justfile. Needs `node` on the path.
- `just check`: run ruff formatting/linting and strict mypy.
- `just pytest`: run only the pytest suite.
- `just examples`: run example proofs only.
- `just ui-test`: `just node-test` (syntax-check the web UI scripts and run
  their node tests in `tests/web/`) plus the pytest files that serve them; a
  subset of `just test`. CI runs only this for pull requests that touch
  nothing but `src/clauz3/{sessions,approvals}/web/` and `tests/web/`;
  prose-only PRs run `just docs`.
- `uv run clauz3 prove --trusted-root tools/email/trusted cases/only_bob_pass.py`
  from an example directory: prove one entry file against a trusted root.

## Coding Style & Naming Conventions

Use Python 3.11+ syntax and 4-space indentation. Ruff enforces linting,
formatting, import order, and selected modernization rules. Mypy runs in strict
mode for `src`, `tests`, and `examples`; `_vendor` is excluded for now. Keep
public package imports as `clauz3`, not the repository name. Trusted example
modules should use `tools/<domain>/trusted/{effects.py,contracts.py}`.

## Testing Guidelines

Tests use pytest and are named `tests/test_*.py`. Add focused pytest cases for
core behavior and example case files for proof behavior. Cases under
`examples/*/cases/` should be valid Python and remain mypy-clean. Example
Justfiles should include both expected-pass and expected-fail proof commands.
Run `just test` before pushing.

## Commit & Pull Request Guidelines

Recent commits use short imperative subjects, for example `Add CI and scalable
mypy roots` or `Rename package to clauz3`. Keep commits scoped. PRs should
include a concise summary, user-visible impact, and validation results such as
`just test`. Link issues when applicable. Screenshots are not needed unless a
future change adds UI.

## Trust Boundary Notes

Only code under trusted roots should define `@clauz3.trusted` effects or
`@contract` helpers. Agent-authored entry files should import trusted tools and
state guarantees with `@clauz3.guarantee(...)`. Do not make untrusted code a
trusted boundary just to satisfy a proof.

A guarantee's argument must be a call to a registered `@contract` helper with
literal (or `clauz3.Row` class) arguments; anything else is refused at prove
time. See "The guarantee grammar" in `docs/reference/python-subset.md`.

`@contract` helper bodies execute as ordinary Python at prove time; only the
relation lambdas inside them are compiled and fail closed. Keep helper bodies
to spec construction and review them as trusted code. See the trust-boundary
section of `docs/explanation/concepts.md`.
