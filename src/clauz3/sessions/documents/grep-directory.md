# Search UTF-8 files in a directory

Requires the bundled filesystem, find, and grep libraries. This recipe iterates
all discovered files without a numeric cap. It searches for a literal substring
and prints each matching line with its path. Use it for a directory of readable
UTF-8 files: binary or unreadable files can abort the run, and an error means
the search is incomplete. Symbolic links are not physical confinement boundaries.

The path guard is deliberately conservative. Paths outside the lexical prefix
or containing two consecutive dots are reported as skipped, including harmless
names such as `report..txt`. Any skip means the search was partial.

```python
import clauz3
from tools.find.trusted.effects import find
from tools.find.trusted import contracts as discovery
from tools.grep.trusted.effects import grep
from tools.grep.trusted import contracts as search

@clauz3.guarantee(discovery.only_read_under("./data/"))
@clauz3.guarantee(discovery.searches_at_most(1))
@clauz3.guarantee(search.only_read_under("./data/"))
@clauz3.guarantee(search.only_pattern("hello"))
def main() -> None:
    for path in find("./data/", ["-type", "f"]):
        if path.startswith("./data/") and ".." not in path:
            for line in grep("hello", path):
                print(path, line)
        else:
            print("Search incomplete; skipped unsupported path:", path)
```
