# text

Read a selected UTF-8 text line without requiring string splitting in the
restricted Python subset. Install with `clauz3 install stdlib:text` (the
filesystem contract dependency is installed automatically).

`tools.text.trusted.effects.read_line(path, line_number) -> str` uses one-based
line numbers, removes the line terminator (LF, CRLF or CR), preserves other
whitespace, and returns an empty string for a blank or missing line. Invalid
UTF-8 and filesystem errors propagate. The path must be nonempty, contain no
parent-directory segments, and the line number must be positive.

`tools.text.trusted.contracts` supplies `only_read_under(root)`,
`never_read_under(prefix)`, `reads_at_most(count)`, and `no_guarantees()`.
These policies govern **read_line only**; other effects need their own contracts.
Path policies follow the filesystem layer's literal prefix semantics; use a
trailing slash for a directory prefix. They do not resolve symlink targets.

```python
import clauz3
from tools.text.trusted import contracts as text
from tools.text.trusted.effects import read_line


@clauz3.guarantee(text.only_read_under("input/"))
@clauz3.guarantee(text.reads_at_most(1))
def main() -> None:
    print(read_line("input/message.txt", 1))
```

Run the positive and negative proof fixtures with `just -f tests/Justfile test`.
