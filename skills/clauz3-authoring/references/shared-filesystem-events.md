# Sharing filesystem events across libraries

Yes: a bioinformatics layer can declare the **same** `Read` and `Write` events
used by `stdlib:filesystem`, and filesystem contracts will constrain those
calls too. A Python import and `@performs` declaration establish this connection;
the library's name and Deal's `read`/`write` markers do not.

Declare the dependency in `tools/bio/clauz3.yaml`:

```yaml
dependencies:
  - stdlib:filesystem
```

Install both libraries and select both trusted roots when proving or running.
Workspace selection includes declared dependencies. For direct CLI proofs,
provide both roots, for example `--trusted-roots tools/bio/trusted
.clauz3/tools/filesystem/trusted --import-root .`. Source-layout tests may use
separate import roots instead. Keep the imported filesystem version consistent
with the version against which the layer was reviewed.

## Declare every relevant operation

```python
# tools/bio/trusted/effects.py
import clauz3
from clauz3.events import performs
from tools.filesystem.trusted.events import Read, Write


@clauz3.trusted
@performs(lambda input_path, output_path: [Read(input_path), Write(output_path)])
def convert_alignment(input_path: str, output_path: str) -> None:
    """Read exactly input_path and modify exactly output_path; implementation required."""
    raise NotImplementedError("Implement and audit all file access before enabling")
```

This interface skeleton deliberately performs no real conversion. Its event
declaration is appropriate only for a body whose file access matches that
description. Do not copy it onto a generic BLAST or samtools shell wrapper
without auditing the wrapper's actual behavior.

```python
# convert.py
import clauz3
from tools.bio.trusted.effects import convert_alignment
from tools.filesystem.trusted import contracts as fs


@clauz3.guarantee(fs.only_reads_files([clauz3.FilePath("input.sam")]))
@clauz3.guarantee(fs.only_writes_files([clauz3.FilePath("output.bam")]))
@clauz3.guarantee(fs.writes_at_most(1))
def main() -> None:
    convert_alignment("input.sam", "output.bam")
```

Changing the output argument to `elsewhere.bam` must fail the proof. Adding
`fs.read_only()` must fail too. Test both, alongside a positive case. The older
`only_write_under("out")` also sees these events but uses a **literal prefix**,
so `outside.bam` matches. Prefer `only_writes_files([...])` for exact filenames
or `only_writes_files_under(clauz3.DirectoryPath("out"))` for a directory boundary.

## Limits of the declaration

- Import the event classes from `tools.filesystem.trusted.events`; defining
  another class called `Write` creates a different relation. Do not vendor a
  second event definition or assume that matching field names joins them.
- Put one `@performs` directly above the function definition, below
  `@clauz3.trusted`. Its lambda may use a subset of parameters, in any order.
  Event fields must be direct required parameters of matching types; literals,
  computed paths, defaulted parameters, and variadic parameters are refused.
- Account for reference databases and their component files, inputs, indexes,
  logs, temporary files, caches, directory traversal, deletions, and moves.
  Pass relevant paths explicitly. Restrict subprocess options and configure
  scratch locations so undeclared writes cannot escape through defaults.
- Use `RecursiveRead` or `RecursiveWrite` for a directory and its descendants;
  exact-file contracts deliberately reject those broad events. A required
  `scratch_dir: str` can declare `RecursiveWrite(scratch_dir)`. One such event
  is one declaration, **not a count of physical files or syscalls**. If fixed
  parameter-based declarations cannot faithfully describe a tool, redesign
  its interface or expose a narrower, honestly documented contract.
- Path declarations do not by themselves reject traversal or confine symlinks.
  Add runtime validation and matching provable preconditions. Reusing events
  does not inherit the preconditions of the filesystem library's functions.
- `Write(path)` provides no content field. It can participate in path/write-count
  policies but cannot justify a content guarantee. Use `WriteText(path, text)`
  only if the caller supplies the actual text and the body writes that text.
- These are trusted declarations, not filesystem tracing or a sandbox. A proof
  cannot discover a subprocess write omitted from `@performs`. Filesystem
  contracts also say nothing about network requests or other resource effects.

Runtime tests should mock or isolate subprocesses and verify their complete
arguments, output/scratch routing, and failure behavior. Avoid real bioinformatics
jobs or network calls merely to validate this interface pattern.
