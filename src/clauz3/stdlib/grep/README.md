# grep

A trusted `clauz3` layer that searches files for a substring — a real
(non-mock) substitute for an agent's `ripgrep` tool.

grep **builds on `filesystem`**. A grep call reads a file, so it carries the
same `read` marker as `read_file`, and the filesystem read policies
(`only_read_under`, `never_read_under`) apply to grep calls unchanged. grep
adds its own search-specific contracts on top.

## Install

grep depends on the `filesystem` layer, so install both:

```bash
clauz3 install stdlib:filesystem
clauz3 install stdlib:grep
```

Prove a program against both trusted roots:

```bash
clauz3 prove \
  --trusted-roots tools/grep/trusted tools/filesystem/trusted \
  plan.py
```

## Effect

Import with `from tools.grep.trusted.effects import grep`:

- `grep(pattern: str, path: str) -> list[str]` — return the lines in `path`
  that contain the substring `pattern`. Precondition: both `pattern` and `path`
  are non-empty. Recorded under the `read` marker.

## Contracts

Import with `from tools.grep.trusted import contracts as grep_rules`:

- `grep_rules.no_guarantees()` — explicit null contract.
- `grep_rules.only_read_under(root)` — every search reads a file under `root`.
- `grep_rules.never_read_under(prefix)` — no search reads under `prefix`.
- `grep_rules.searches_at_most(count)` — at most `count` searches occur.
- `grep_rules.only_pattern(pattern)` — every search uses exactly `pattern`.

`only_read_under` and `never_read_under` delegate to the filesystem read
policies, so they also constrain any `read_file` calls in the same program.

## Example

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

A search of `/etc/shadow` would fail the `only_read_under("/repo")` proof.

## Tests

```bash
just -f tests/Justfile test
```

The grep test Justfile points at both the grep and filesystem layers, since
grep imports filesystem.
