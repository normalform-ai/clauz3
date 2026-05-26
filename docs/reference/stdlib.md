# Standard Library Tools

`clauz3` ships a small set of **real (non-mock) tools** under
`src/clauz3/stdlib`. Unlike the layers under `examples/`, their effect bodies
do real work: the prover skips them, but `clauz3 run` executes them after a
program is proved and approved.

After `pip install clauz3`, install one into the current repo with the
`stdlib:` scheme:

```bash
clauz3 install stdlib:filesystem
clauz3 install stdlib:grep
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
  tools/<name>/trusted/   # the installable trusted layer (effects + contracts)
  tests/
    cases/                # *_pass.py must prove, *_fail.py must not
    Justfile              # proves the cases — the tool's guarantees
  README.md
```

The repo's `just stdlib` target runs every stdlib tool's `tests/Justfile`.

## filesystem

`read_file` and `write_file`, plus path-prefix policies over where a program
may read and write. Reads carry deal's `read` marker and writes carry `write`,
so the contracts can constrain each independently.

| Contract | Guarantee |
| --- | --- |
| `read_only()` | no writes |
| `no_reads()` | no reads |
| `only_read_under(root)` | every read path is under `root` |
| `only_write_under(root)` | every write path is under `root` |
| `never_read_under(prefix)` | no read path is under `prefix` |
| `never_write_under(prefix)` | no write path is under `prefix` |
| `writes_at_most(count)` | at most `count` writes |

The prefix policies compile to `str.startswith` over the symbolic call
argument (see [Python subset](python-subset.md)).

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file, write_file


@clauz3.guarantee(fs.only_read_under("/repo"))
@clauz3.guarantee(fs.only_write_under("/repo/build"))
def main() -> None:
    source = read_file("/repo/src/app.py")
    write_file("/repo/build/app.txt", source)
```

## grep

A substitute for an agent's `ripgrep`. `grep(pattern, path)` reads a file, so it
carries the same `read` marker as `read_file` and **imports the `filesystem`
layer**: its `only_read_under` / `never_read_under` contracts delegate to the
filesystem read policies, which therefore also govern grep calls. grep adds
search-specific contracts:

| Contract | Guarantee |
| --- | --- |
| `only_read_under(root)` | every search reads under `root` |
| `never_read_under(prefix)` | no search reads under `prefix` |
| `searches_at_most(count)` | at most `count` searches |
| `only_pattern(pattern)` | every search uses exactly `pattern` |

Because grep imports filesystem, install both:

```bash
clauz3 install stdlib:filesystem
clauz3 install stdlib:grep
```

```python
import clauz3
from tools.grep.trusted import contracts as grep_rules
from tools.grep.trusted.effects import grep


@clauz3.guarantee(grep_rules.only_read_under("/repo"))
@clauz3.guarantee(grep_rules.searches_at_most(3))
def main() -> None:
    grep("TODO", "/repo/src/app.py")
    grep("FIXME", "/repo/src/util.py")
```
