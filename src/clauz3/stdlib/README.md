# clauz3 standard library

Trusted tool layers that ship with `clauz3`. Unlike the worked examples under
`examples/`, these are **real, non-mock** tools intended for use in actual
projects, and they are installable with the `stdlib:` scheme (resolved via
`importlib.resources`, so it works from a source checkout or a `pip install`ed
wheel):

```bash
clauz3 install stdlib:filesystem
clauz3 install stdlib:google_workspace --skills
clauz3 install stdlib:git
```

Each tool directory is self-contained:

```
<name>/
  clauz3.yaml              # optional package manifest for shared prerequisites
  tools/<domain>/
    clauz3.yaml            # domain description and setup requirements
    trusted/              # effects + contracts
  tests/
    cases/                # *_pass.py must prove, *_fail.py must not
    Justfile              # proves the cases (the tool's guarantees)
  README.md
```

There is intentionally less at the top level than an example: no top-level
`cases/` or `Justfile`. The proof cases live under `tests/` and are run by the
repo's `just stdlib` target.

Installation copies each `tools/<domain>/` and, when present, snapshots shared package
requirements beside the domain manifest. Root-level tests and guides remain in
the distribution. Every bundled domain ships a manifest. External legacy
libraries may omit it and install with a warning; the root package manifest is
always optional. See [Library manifests and setup](../../../docs/reference/library-manifests.md).

A bundle name can differ from its domain names: `google_workspace/` contains
`tools/google_workspace/gmail/`, with future services beside Gmail. The root
manifest names the bundle and owns shared setup; each service owns its manifest,
trusted root, and contracts. Workspace selections enable services independently.

## Tools

| Tool | What it does | Headline policies |
| --- | --- | --- |
| [`filesystem`](filesystem) | read, search, list, write, copy, move, delete and chmod files | `never_read_under`, `only_write_under`, `read_only`, `no_deletes` |
| [`google_workspace`](google_workspace) | save Gmail drafts, preview and send through gog | `drafts_only`, `only_recipients`, `emails_at_most`, `dry_run_only` |
| [`db`](db) | read DuckDB/CSV/Parquet; create DuckDB tables and insert typed rows | `only_read_under`, `only_writes_databases`, `only_writes_tables`, `rows_inserted_at_most` |
| [`git`](git) | run local `git` subcommands and pushes | `read_only`, `never_subcommands`, `only_push_to_branch` |
| [`gh`](gh) | run the GitHub CLI (`gh`) | `read_only`, `only_commands`, `only_repo` |
| [`rsync`](rsync) | synchronize files with `rsync` | `only_dest_under`, `no_delete`, `syncs_at_most` |
| [`env`](env) | read environment variables | `only_vars`, `never_vars` |
| [`web_fetch`](web_fetch) | fetch URLs and send HTTP requests | `only_fetch_under`, `https_only`, `no_posts` |
| [`web_search`](web_search) | search the web | `searches_at_most`, `no_query_contains` |

`filesystem` states its policies over *events* rather than functions (see its
README): every tool that reads a file performs a `Read`, so one
`never_read_under` covers `read_file`, `grep`, and the source of a `copy` or
`move`. The other libraries still state policies over their own effects;
`git`, `rsync` and `db` also touch files, and a filesystem policy does not yet
cover them.

## Path safety

Trusted effects with path-like arguments reject lexical `..` components
before proof or execution. The check recognizes POSIX separators, Windows
separators, mixed-separator paths, and the Windows drive delimiter — `C:..`
is a drive-relative parent reference even with no separator before the dots.
It applies across filesystem, database, git, rsync and web-fetch URL paths. This prevents a raw prefix guarantee such
as `only_write_under("/sandbox")` from accepting `/sandbox/../etc/passwd`.

URL effects go further, because a remote decoder can reconstruct a traversal
from parts that individually look harmless. A `..` segment needs a boundary on
*both* sides, so rather than enumerate boundary positions these effects refuse
the encodings themselves: encoded dots (`%2e`), encoded separators (`%2f`,
`%5c`), and a trailing `..` closed by `?` or `#`.

Refusing those alone would only cover a single decoding pass. A token can also
be *synthesised* at greater depth — `%%32%65` decodes to `%2e`, then to `.`,
with no `%2e` or `..` anywhere in the original. Every such synthesis needs a
`%` (only from `%25`) or one of the hex digits spelling `2e`/`2f`/`5c` (only
from `%32`, `%35`, `%43`, `%63`, `%45`, `%65`, `%46`, `%66`), so those are
refused too. That closes the construction at every depth: assembling a
dangerous token without them requires it to be present literally, where the
direct checks catch it.

This is stricter than a URL parser — a legitimate query string containing any
of those tokens is refused rather than analysed — but it is not a blanket ban
on encoding: `%20` and `%2C` still pass, because their decoded bytes cannot
spell a traversal token. Decoders that invent non-standard separator spellings
(overlong UTF-8, `;` path parameters) are out of scope; the guarantee is
against repeated, standards-compliant percent-decoding.

Arguments that name a single file inside an already-validated directory — such
as `read_rows`'s `table`, which becomes `<dir>/<table>.csv` — must be one path
component. Rejecting `..` alone is not sufficient there: a bare separator
reaches a subdirectory, and an absolute or drive-qualified value discards the
directory entirely, since `Path("/data") / "/tmp/x"` is `/tmp/x` and
`PureWindowsPath("C:/data") / "D:x"` is `D:x`.

This is a lexical guarantee. It does not resolve filesystem symlinks; trusted
layers that need physical containment must additionally constrain or resolve
symlink targets at their runtime boundary.

## Running the tests

From the repo root:

```bash
just stdlib
```

or one tool at a time:

```bash
just -f src/clauz3/stdlib/filesystem/tests/Justfile test
```
