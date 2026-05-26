# Repository Guidelines

## Project Structure & Module Organization

`src/clauz3/` contains the CLI, prover, contract-spec DSL, and vendored
`deal_solver` code under `_vendor/`. Tests live in `tests/`. Worked examples
live in `examples/<domain>/`: each has `cases/` for agent-authored proof
fixtures and `tools/<domain>/trusted/` for trusted effects and contracts.
Design notes are in `docs/` and `docs/todos/`. CI is defined in
`.github/workflows/main.yaml`.

## Build, Test, and Development Commands

- `uv sync --dev`: install the project and development tools.
- `just test`: run pytest, ruff, mypy, and every example Justfile.
- `just check`: run ruff formatting/linting and strict mypy.
- `just pytest`: run only the pytest suite.
- `just examples`: run example proofs only.
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

Only code under trusted roots should define `@deal.has(...)` effects or
`@contract` helpers. Agent-authored entry files should import trusted tools and
state guarantees with `@clauz3.guarantee(...)`. Do not make untrusted code a
trusted boundary just to satisfy a proof.
