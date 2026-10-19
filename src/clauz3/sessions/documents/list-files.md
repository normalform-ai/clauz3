# List all file names under a directory

Requires the bundled `filesystem` library. Prints the complete
returned list without attempting to iterate a symbolic `list[str]`. This lists
names; it does not search file contents. Adapt the literal directory path to
the request. The path guarantee is lexical, not physical symlink confinement.

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import find

@clauz3.guarantee(fs.only_reads_files_under(clauz3.DirectoryPath("./data")))
@clauz3.guarantee(fs.lists_at_most(1))
def main() -> None:
    print(find("./data/", ["-type", "f"]))
```
