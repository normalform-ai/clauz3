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
| `grep` | Substring search over files; shares the filesystem `read` marker. | [grep](../stdlib/grep.md) |
| `editor` | In-place file edit and append with path-prefix + content-substring guards. | [editor](../stdlib/editor.md) |
| `web_fetch` | HTTP GET via `urllib`, with URL-prefix and exfil-style URL-content guards. | [web_fetch](../stdlib/web_fetch.md) |
| `web_search` | Search via a configurable JSON backend, with query-content guards. | [web_search](../stdlib/web_search.md) |
| `env` | Read environment variables with name allowlist / blocklist / prefix vetoes. | [env](../stdlib/env.md) |

Each per-tool doc page renders the tool's `README.md` and inlines the
trusted layer's source, so the doc tracks the code without a hand-maintained
API table. Adding a new stdlib tool requires one README in the tool
directory plus two thin stub pages (`docs/stdlib/<tool>.md` and
`docs/stdlib/<tool>-all-cases.md`) and three nav lines.

## Composition

Marker-named relations (`Read = effect("read")`, `Write = effect("write")`)
cross-cut tools that carry the same marker. For example, `stdlib:grep`
imports `stdlib:filesystem`'s `read` marker, so a `fs.only_read_under("/repo")`
guarantee constrains both `read_file` and `grep` calls. Similarly,
`stdlib:editor` carries both `edit` and `write` markers, so installing both
`editor` and `filesystem` stacks their respective write policies.
