# clauz3 standard library

Trusted tool layers that ship with `clauz3`. Unlike the worked examples under
`examples/`, these are **real, non-mock** tools intended for use in actual
projects, and they are installable with the `stdlib:` scheme (resolved via
`importlib.resources`, so it works from a source checkout or a `pip install`ed
wheel):

```bash
clauz3 install stdlib:filesystem
clauz3 install stdlib:grep
```

Each tool directory is self-contained:

```
<name>/
  tools/<name>/trusted/   # the installable trusted layer (effects + contracts)
  tests/
    cases/                # *_pass.py must prove, *_fail.py must not
    Justfile              # proves the cases (the tool's guarantees)
  README.md
```

There is intentionally less at the top level than an example: no top-level
`cases/` or `Justfile`. The proof cases live under `tests/` and are run by the
repo's `just stdlib` target.

## Tools

| Tool | What it does | Headline policies |
| --- | --- | --- |
| [`filesystem`](filesystem) | read and write files | `read_only`, `only_write_under`, `never_read_under` |
| [`grep`](grep) | search files for a substring (imports `filesystem`) | `only_read_under`, `searches_at_most`, `only_pattern` |

## Running the tests

From the repo root:

```bash
just stdlib
```

or one tool at a time:

```bash
just -f src/clauz3/stdlib/filesystem/tests/Justfile test
```
