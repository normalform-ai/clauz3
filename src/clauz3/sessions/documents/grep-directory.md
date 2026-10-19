# Search UTF-8 files in a directory

Requires the bundled `filesystem` library. This recipe iterates
all discovered files without a numeric cap. It searches for a literal substring
and prints each matching line with its path. Use it for a directory of readable
UTF-8 files: binary or unreadable files can abort the run, and an error means
the search is incomplete. Symbolic links are not physical confinement boundaries.

The path guard is deliberately conservative. Paths outside the lexical prefix,
or containing `..`, `/.`, `//` or a backslash, are reported as skipped. That includes
harmless names such as `report..txt` and hidden files such as `.env`. Any skip
means the search was partial.

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import find, grep

@clauz3.guarantee(fs.only_reads_files_under(clauz3.DirectoryPath("./data")))
@clauz3.guarantee(fs.lists_at_most(1))
@clauz3.guarantee(fs.only_pattern("hello"))
@clauz3.guarantee(fs.read_only())
def main() -> None:
    for path in find("./data/", ["-type", "f"]):
        if (
            path.startswith("./data/")
            and ".." not in path
            and "/." not in path
            and "//" not in path
            and "\\" not in path
        ):
            for line in grep("hello", path):
                print(path, line)
        else:
            print("Search incomplete; skipped unsupported path:", path)
```
