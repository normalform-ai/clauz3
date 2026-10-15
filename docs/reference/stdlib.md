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
| `filesystem` | Read and write files with path-prefix policies. | [filesystem](../stdlib/filesystem.md) |
| `text` | Read a selected UTF-8 line with path and call-count policies. | [text](../stdlib/text.md) |
| `grep` | Substring search over files; applies the filesystem read policies to its own effect. | [grep](../stdlib/grep.md) |
| `db` | Read typed rows from DuckDB, CSV, Parquet, or folders via DuckDB. | [db](../stdlib/db.md) |
| `editor` | In-place file edit and append with path-prefix + content-substring guards. | [editor](../stdlib/editor.md) |
| `web_fetch` | HTTP GET/POST via `urllib`, with URL-prefix, no-POST, and exfil-style URL-content guards. | [web_fetch](../stdlib/web_fetch.md) |
| `web_search` | Search via a configurable JSON backend, with query-content guards. | [web_search](../stdlib/web_search.md) |
| `env` | Read environment variables with name allowlist / blocklist / prefix vetoes. | [env](../stdlib/env.md) |
| `git` | Run local `git` subcommands and pushes with subcommand / location / push-target policies. | [git](../stdlib/git.md) |
| `gh` | Run the GitHub CLI with verb / object / repository policies. | [gh](../stdlib/gh.md) |
| `find` | List files under a tree; applies the filesystem read policies to its own effect. | [find](../stdlib/find.md) |
| `rsync` | Sync files with destination / source / no-delete policies. | [rsync](../stdlib/rsync.md) |
| `coreutils` | Delete/move/copy/chmod (`rm`, `mv`, `cp`, `chmod`) with path + mode policies. | [coreutils](../stdlib/coreutils.md) |

Each per-tool doc page renders the tool's `README.md` and inlines the
trusted layer's source, so the doc tracks the code without a hand-maintained
API table. Adding a new stdlib tool requires one README in the tool
directory plus two thin stub pages (`docs/stdlib/<tool>.md` and
`docs/stdlib/<tool>-all-cases.md`) and three nav lines.

## Composition

A layer composes with another by *saying so*. `stdlib:grep` and `stdlib:find`
import `stdlib:filesystem` and apply its path rule to their own effect:

```python
# grep/trusted/contracts.py
@contract
def only_read_under(root: str) -> ContractSpec:
    return filesystem.reads_under(Grep, root)
```

So `grep.only_read_under("/repo")` is the same rule as
`filesystem.only_read_under("/repo")`, stated about grep's effect, and an
agent using both layers states both.
