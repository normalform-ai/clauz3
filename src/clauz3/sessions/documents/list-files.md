# List all file names under a directory

Requires the bundled `filesystem` and `find` libraries. Prints the complete
returned list without attempting to iterate a symbolic `list[str]`. This lists
names; it does not search file contents. Adapt the literal directory path to
the request. The path guarantee is lexical, not physical symlink confinement.

```python
import clauz3
from tools.find.trusted import contracts as listing
from tools.find.trusted.effects import find

@clauz3.guarantee(listing.only_read_under("./data/"))
@clauz3.guarantee(listing.searches_at_most(1))
def main() -> None:
    print(find("./data/", ["-type", "f"]))
```
