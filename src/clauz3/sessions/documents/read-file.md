# Read one UTF-8 file

Requires the bundled `filesystem` library. Replace `./data/input.txt` with the
requested file and use that exact file in the guarantee.

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file

@clauz3.guarantee(fs.only_reads_files([clauz3.FilePath("./data/input.txt")]))
@clauz3.guarantee(fs.read_only())
def main() -> None:
    print(read_file("./data/input.txt"))
```
