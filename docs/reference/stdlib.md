# Standard library tools

`clauz3` ships a small set of **real (non-mock) tools** under
`src/clauz3/stdlib`. Unlike the layers under `examples/`, their effect
bodies do real work: the prover skips them, but `clauz3 run` executes them
after a program is proved and approved.

## Install

After `pip install clauz3`, install one into the current repo with the
`stdlib:` scheme:

```bash
clauz3 install stdlib:filesystem
clauz3 install stdlib:web_fetch
```

`install stdlib:<name>` locates the bundled tool with `importlib.resources`
(so it works identically from a source checkout or an installed wheel) and
copies its `tools/<name>/` layer into the current project, exactly like
installing from a local path. A bare `clauz3 install <name>` works as a
shorthand.

## Layout

A stdlib tool is deliberately leaner than an example. There is no top-level
`cases/` or `Justfile`; the proof cases live under `tests/`:

```
<name>/
  README.md                     # user-facing prose; also rendered on the per-tool doc page
  tools/<name>/trusted/         # the installable trusted layer (effects + contracts)
  tests/
    cases/                      # *_pass.py must prove, *_fail.py must not
    Justfile                    # proves the cases — the tool's own guarantees
```

The repo's `just stdlib` target runs every stdlib tool's `tests/Justfile`,
and `clauz3 test stdlib:<name>` does the same for a single tool.

## Available tools

| Tool | One-line | Doc |
|---|---|---|
| `filesystem` | Read, search, list, write, copy, move, delete and chmod files, with policies over the events each tool performs. | [filesystem](../stdlib/filesystem.md) |
| `db` | Read typed rows from DuckDB, CSV, Parquet, or folders via DuckDB. | [db](../stdlib/db.md) |
| `web_fetch` | HTTP GET/POST via `urllib`, with URL-prefix, no-POST, and exfil-style URL-content guards. | [web_fetch](../stdlib/web_fetch.md) |
| `web_search` | Search via a configurable JSON backend, with query-content guards. | [web_search](../stdlib/web_search.md) |
| `env` | Read environment variables with name allowlist / blocklist / prefix vetoes. | [env](../stdlib/env.md) |
| `git` | Run local `git` subcommands and pushes with subcommand / location / push-target policies. | [git](../stdlib/git.md) |
| `gh` | Run the GitHub CLI with verb / object / repository policies. | [gh](../stdlib/gh.md) |
| `rsync` | Sync files with destination / source / no-delete policies. | [rsync](../stdlib/rsync.md) |

Each per-tool doc page renders the tool's `README.md` and inlines the
trusted layer's source, so the doc tracks the code without a hand-maintained
API table. Adding a new stdlib tool requires one README in the tool
directory plus two thin stub pages (`docs/stdlib/<tool>.md` and
`docs/stdlib/<tool>-all-cases.md`) and three nav lines.

## Composition

`filesystem` states its contracts over [events](events.md), not functions.
Each effect declares with `@performs` what it does to the filesystem, and a
contract over an event covers every effect that performs it:

```python
@performs(lambda path: Read(path))
def grep(pattern: str, path: str) -> list[str]: ...


@contract
def never_read_under(prefix: str) -> ContractSpec:
    return Read.all(lambda e: not e.path.startswith(prefix) and ...)
```

So `fs.never_read_under("/repo/.env")` refuses `read_file`, `grep`, and a
`copy` or `move` from that path, and a new tool that performs `Read` is covered
without changing the contract. The other stdlib libraries still state policies
over their own effects; `git`, `rsync` and `db` also touch files, and a
filesystem policy does not yet cover them.
