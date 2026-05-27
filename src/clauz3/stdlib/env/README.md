# env

A trusted `clauz3` layer for reading environment variables with name-based
allowlists, blocklists, prefix vetoes, and a read-count bound — the
canonical guard for any program that handles credentials via `os.environ`.

Unlike the layers under `examples/`, this effect is **real**, not a mock:
`read_env` actually calls `os.environ.get`. The prover never runs the body
— it records each call as a fact and proves the contract. The body runs
only under `clauz3 run`, after a program is proved and approved.

## Install

```bash
clauz3 install stdlib:env
```

## Effects

Import with `from tools.env.trusted.effects import read_env`:

- `read_env(name: str) -> str` — return env var `name`, or the empty
  string if unset. Precondition: `name` is non-empty. Recorded under the
  `read` and `env` markers.

## Contracts

Import with `from tools.env.trusted import contracts as envc`:

- `envc.no_guarantees()` — explicit null contract.
- `envc.no_env_reads()` — the program reads no env vars.
- `envc.only_vars(allowlist)` — every var name read is in `allowlist`.
- `envc.never_vars(blocklist)` — no var name read is in `blocklist`.
- `envc.never_var_prefix(prefix)` — no var name starts with `prefix`.
- `envc.env_reads_at_most(count)` — at most `count` reads occur.

Prefer `only_vars` when the needed variables are small and explicit
(strongest). `never_var_prefix("SECRET_")` is a convenient blanket veto.

## Example

```python
import clauz3
from tools.env.trusted import contracts as envc
from tools.env.trusted.effects import read_env


@clauz3.guarantee(envc.only_vars(["GITHUB_REPO", "OPENAI_BASE_URL"]))
@clauz3.guarantee(envc.never_var_prefix("AWS_"))
def main() -> None:
    repo = read_env("GITHUB_REPO")
    base = read_env("OPENAI_BASE_URL")
```

## Tests

```bash
clauz3 test stdlib:env
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not.
