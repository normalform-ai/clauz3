# Count logical lines in a UTF-8 file

Requires the bundled `filesystem` library. Replace `./data/input.txt` with the
requested file and use that exact file in the guarantee.

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_lines

@clauz3.guarantee(fs.only_reads_files([clauz3.FilePath("./data/input.txt")]))
@clauz3.guarantee(fs.read_only())
def main() -> None:
    print(len(read_lines("./data/input.txt")))
```

`len()` works on returned lists. `read_lines` uses Python `str.splitlines()`:
empty files have zero lines, blank lines count, and a final unterminated line
counts. CRLF is one separator; Unicode line separators are recognized too.
For example, `hello` is one logical line, while `hello\n\n` is two.

This is not `wc -l`, which counts LF bytes. If the user requests precisely that
behavior, do not substitute logical lines silently. UTF-8 decoding and file I/O
errors abort the run; do not claim a count after an error. The library loads
the entire file into memory. `str.splitlines()` remains unsupported in submitted
programs; use the installed effect above.
