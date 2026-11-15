# Filesystem edit and search

## Goal

Make light code editing practical through `stdlib:filesystem`, so switching to
an agent's native tools is needed less often. Today an edit means rewriting a
whole file with `write_file` (the Python subset has no `str.replace`), and
`grep` matches a substring in one file. Claude's native Edit and Grep do both
in one call.

This is sub-project 1 of three. The others, an `@clauz3.unrestricted` marker
with a powertools library and switchable agent modes, get their own specs.

The new tools perform existing events, so every current filesystem contract
covers them unchanged. `only_writes_files_under(DirectoryPath("src"))` then
proves that an agent edits only under `src/`, which native tools cannot promise.

## `edit_file`

```python
@performs(lambda path, new: [Read(path), WriteText(path, new)])
def edit_file(path: str, old: str, new: str, replace_all: bool = False) -> None
```

- Replaces `old` with `new` in the UTF-8 file at `path`.
- `old` must be non-empty and differ from `new` (preconditions).
- Without `replace_all`, `old` must occur exactly once. Zero or several
  occurrences raise `ValueError` naming the count, and the file is unchanged.
  With `replace_all=True`, every occurrence is replaced and at least one must
  exist.
- The file must already exist; `edit_file` never creates one.
- Line endings and encoding are preserved: read and write with `newline=""`.
  The write goes to a temporary file in the same directory, then `os.replace`,
  so a failure never leaves a half-written file. The temporary is named
  `NAME.<random>.tmp` (no leading dot), so its path starts with the edited
  path and every path-prefix contract that admits the file admits it too. The file's permission bits
  are kept.
- Path preconditions match the other effects (`is_plain_path`, non-empty).
- Returns `None`, like `write_file`.

### What contracts see

The call is a `Read` of `path` and a `WriteText` of `path` whose `text` is
`new`. Consequences, to be stated in the docstring and README:

- Path contracts (`read_only`, `only_writes_files`,
  `only_writes_files_under`, `never_write_under`, `writes_at_most`, and the read
  contracts) apply exactly.
- `must_not_write(s)` and `write_length_at_most(n)` check the inserted text
  `new`, not the resulting file. Removing text can join neighbours into `s`
  (`"se" + "x" + "cret"` with `x` deleted), and that is not caught. This is the
  same scope as `append_file`, whose `text` is also only the added part.

## `search_files`

```python
class Match(clauz3.Row):
    path: str
    line_number: int
    text: str

@performs(lambda path: RecursiveRead(path))
@clauz3.ensures(lambda pattern, path, glob, max_results, result: len(result) <= max_results)
def search_files(pattern: str, path: str, glob: str, max_results: int) -> list[Match]
```

- Runs `rg` recursively under `path` and returns up to `max_results` matching
  lines, each with its path, one-based line number and line text (without the
  terminator).
- `pattern` is a ripgrep (Rust) regular expression. `glob` restricts file names
  (for example `*.py`); an empty string means all files.
- `max_results` must be between 1 and 1000. Results stop at the cap, so the
  argument-aware `ensures` lets loops over results prove against count
  contracts, as with `search_messages`.
- ripgrep defaults apply and are documented: `.gitignore`d and binary files
  are skipped, hidden files are skipped unless `glob` matches them (ripgrep
  applies a `--glob` whitelist before its hidden rule, so `glob="*"` searches
  them), and symlinks are not followed. No flag to change this in
  this version. Ignore files are honoured only at or below `path`: a
  `.gitignore` above `path`, the global `core.excludesFile`, and
  `.git/info/exclude` are not read, so a repository-root `.gitignore` applies
  when searching `.` but not when searching a subdirectory of it. A
  `.gitignore` at or below `path` applies whenever `path` is inside a git
  repository, wherever its root is (checked with rg 14.1.0 and 15.1.0);
  outside a repository only `.ignore` and `.rgignore` apply. Narrow with
  `glob` instead.
- Invocation: `rg --no-config --no-ignore-parent --no-ignore-global
  --no-ignore-exclude --json -e PATTERN [--glob GLOB] -- ROOT`, with an
  explicit argument vector. `-e` and `--` stop a pattern or path that starts
  with `-` being read as an option, and relative roots are prefixed with `./`
  as `find` does. `--no-config` ignores the user's ripgrep configuration
  (`RIPGREP_CONFIG_PATH` and the rest): a config could turn on `--follow` and
  traverse symlinks `search_files` is meant to leave alone, include files the
  project's `.gitignore` excludes, or run arbitrary commands via `--pre`.
  `--no-ignore-parent`, `--no-ignore-global` and `--no-ignore-exclude` keep
  ripgrep from reading any ignore file outside `path` -- otherwise ripgrep
  ascends from the directory it is run in (not from `path`) looking for
  `.gitignore` files, which could hide a match under `path` because of a rule
  that lives above it, outside what `RecursiveRead(path)` declares was read.
  Exit status 1 (no matches) returns `[]`; status 2 raises.
- `RecursiveRead(path)` already means "a path that may be a directory and its
  descendants", so `never_read_under` and `only_reads_files_under` treat a
  search exactly as they treat a recursive copy source.

The existing `grep(pattern, path)` stays unchanged for compatibility.

### Requirement

`rg` becomes a tool requirement in `tools/filesystem/clauz3.yaml`, scoped to
`search_files` the way `find` is scoped to `find`. Other filesystem effects do
not need it.

## Documentation

- Docstrings on both effects and on `Match` (the API catalogue shows them).
- README and the bundle's agent guide: an "Edit code" section with a worked
  program, the inserted-text caveat, and the ripgrep defaults.
- `Match` lives in `tools/filesystem/trusted/schemas.py`, following the gmail
  bundle; add that file to `docs/stdlib/filesystem.md`.

## Testing

- Proof cases (pass and fail), wired into the bundle Justfile:
  - edits under an allowed directory prove `only_writes_files_under`; an edit
    outside it fails;
  - `read_only()` fails for `edit_file`;
  - `never_read_under` fails for a `search_files` whose path is a prefix of the
    forbidden one;
  - writing once per search result proves against
    `writes_at_most(max_results)`;
  - preconditions: empty `old`, `old == new`, `max_results` of 0 or 1001.
- Runtime tests on temporary files (no mocks needed for `edit_file`):
  unique replace; zero and multiple occurrences leave the file byte-identical;
  `replace_all`; CRLF preserved; permission bits preserved; missing file
  raises.
- Runtime tests for `search_files` against a small temporary tree with real
  `rg`: line numbers, glob filtering, `.gitignore` respected, the result cap,
  no matches returns `[]`, a pattern starting with `-`.

## Out of scope

- Multi-edit in one call (several edits are several calls).
- Including ignored or hidden files in search.
- Context lines around matches.
- A text-level guarantee over the resulting file contents.
