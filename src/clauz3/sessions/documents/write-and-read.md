# Write a file and read it back

Requires the bundled `filesystem` library. This deliberately composes a write
and a read. Adapt the literal path and contents to the user's request; do not add
the verification read when the user has requested write-only access.

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file, write_file

@clauz3.guarantee(fs.only_writes_files([clauz3.FilePath("./data/result.txt")]))
@clauz3.guarantee(fs.only_reads_files([clauz3.FilePath("./data/result.txt")]))
@clauz3.guarantee(fs.writes_at_most(1))
def main() -> None:
    write_file("./data/result.txt", "hello")
    print(read_file("./data/result.txt"))
```
