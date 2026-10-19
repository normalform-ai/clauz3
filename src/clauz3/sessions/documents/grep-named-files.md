# Search two named files for a literal substring

Requires the bundled `filesystem` library. `grep` matches a literal
substring, not a regex. This complete recipe searches exactly the two named
files, not a directory tree. Each printed list contains all matching lines in
that file. Adapt the literal paths and pattern to the actual request.

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import grep

@clauz3.guarantee(fs.only_reads_files([
    clauz3.FilePath("./data/input.txt"),
    clauz3.FilePath("./data/other.txt"),
]))
@clauz3.guarantee(fs.only_pattern("hello"))
@clauz3.guarantee(fs.reads_at_most(2))
def main() -> None:
    for path in ["./data/input.txt", "./data/other.txt"]:
        print(path, grep("hello", path))
```
