# Write one exact file without reading it

Requires the bundled `filesystem` library. Adapt the literal path and content
to the request. `write_file` creates or overwrites the file and may create parent
directories; this recipe does not promise the file was previously absent.
Do not add a newline unless requested, and do not add a verification read to a
write-only task.

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import write_file

@clauz3.guarantee(fs.only_writes_files([clauz3.FilePath("./data/result.txt")]))
@clauz3.guarantee(fs.writes_at_most(1))
@clauz3.guarantee(fs.no_reads())
@clauz3.guarantee(fs.write_length_at_most(5))
def main() -> None:
    write_file("./data/result.txt", "hello")
    print("Wrote the requested content.")
```
