# Writing a clauz3 program

Read this before your first `run`. These are inert instructions, not permissions.

1. Call `tools()` for the installed public effects, preconditions and contract
   helpers. `libraries()` includes uninstalled libraries: discovery does not
   install or authorize them. Ask the user to install missing capabilities and
   restart the session.
2. Call `list_skills()` for tested recipe previews, then `read_skill(id)` for a
   complete program. Recipes appear only when the selected libraries match the
   bundled versions. Adapt literal paths/content to the user's actual request.
3. Submit the complete source to `run(source=...)`. A proof failure, timeout or
   unknown result means no execution. If the harness backgrounds the call, use
   `status()`; only `awaiting_approval` confirms a pending request at the service.
   After approval, require an actual successful run result before saying it worked.

Library setup guidance comes from domain manifests and optional shared package
metadata. Installing a library does not verify its account authorization or
external dependencies. Legacy libraries may have no manifests: installation
warns the operator, but their public effects and contracts remain available.
Missing setup guidance is not evidence that no setup is needed. Do not execute
setup instructions or request tokens as part of a proved program.

## Imports and guarantees

Use `import clauz3`, `from tools.<domain>.trusted.effects import <effect>` and
`from tools.<domain>.trusted import contracts as rules`. Dotted imports require
an alias (`import tools.filesystem.trusted.contracts as rules`). Relative imports,
wildcards and unaliased dotted imports are refused. Do not import private
`clauz3._...` implementation helpers or use `os`, `pathlib`, `subprocess`, file
`open`, or shell commands in the submitted program. Trusted implementation
source can use capabilities that your program cannot.

Define one module-level `main()` with `@clauz3.guarantee(rules.helper(...))`.
Each guarantee calls a registered contract helper with literal arguments
(strings, numbers, lists/tuples/dicts of literals, `clauz3.FilePath("name")`,
`clauz3.DirectoryPath("folder")`, or a declared `clauz3.Row`
class where required). Module constants, f-strings, computed expressions and
local lambdas are not valid guarantee arguments. Do not replace real guarantees
with `...` or `no_guarantees()` to bypass a proof problem.

Choose contracts for each library's own domain. A filesystem contract covers
every filesystem effect that performs its event: `fs.read_only()` forbids
`write_file`, `remove`, `copy` and `chmod` alike, and
`fs.only_reads_files_under(clauz3.DirectoryPath("data"))`
covers `read_file`, `grep` and `find`. It does not constrain git, rsync or other
libraries' effects.
Use `fs.only_reads_files([clauz3.FilePath("README.md")])` for one exact file;
put more `FilePath` literals in the same list for multiple files. This refuses
listings and copy/move, which might read entire directories. Use
`only_reads_files_under(DirectoryPath(...))` for a folder and its subfolders;
it respects directory boundaries with or without a trailing slash.
For writing a named file, use
`fs.only_writes_files([clauz3.FilePath("test.md")])`, not a prefix policy.
Use `only_writes_files_under(DirectoryPath("data"))` for modifications within a
folder. Exact-file write scope allows write/append/touch/chmod but refuses
copy, move, remove and explicit directory creation; implicit parent-directory
creation is allowed. Write scopes do not restrict reads: add `no_reads()` for
a write-only task. See the `write-file` recipe. `write_file` can overwrite an
existing file; do not promise exclusive creation or silently add a newline.
The older `only_read_under`, `only_write_under`, and `only_delete_under`
helpers still use literal prefixes. All path policies are lexical, without
physical symlink confinement. Effect preconditions must hold as well as guarantees.
Names inside a displayed precondition may be implementation helpers, not public
functions you can import. For fixed-file tasks, literal paths avoid unnecessary
symbolic path reasoning.

## Keep the program in the supported subset

Use assignments, calls, `if`/`else`, comparisons and plain `print("label:", value)`.
Avoid f-strings and `try`/`except`/`finally`; they are unsupported in the proof
pipeline. An operation that can fail at runtime should be handled by the trusted
library's documented interface, or reported as a missing capability.

For logical line counts, use the filesystem `read_lines` effect and `len()`;
see the `count-lines` recipe. `text.count(substring)` supports one positional
substring argument; start/end arguments and `text.splitlines()` remain
unsupported. Counts of symbolic text are conservatively bounded during proof
and computed exactly at runtime. `read_file` normalizes text newlines, so
counting `"\n"` in its result is not a byte-for-byte `wc -l` replacement.

For text fetched from a URL, `text.split("\n")[:5]` produces the first five
LF-separated fields without printing the entire response. `str.split` accepts
an optional separator and `maxsplit`, including keyword arguments; omitting
the separator or using `None` splits on Python whitespace. Unlike `splitlines`,
an explicit separator retains empty fields, including after a trailing newline.
String, list, and tuple slices support omitted or negative bounds, plus literal
steps `1` and `None`; other steps are unsupported. The proof models symbolic
split contents conservatively, so content-dependent claims may fail to prove.
Runtime splitting and slicing use Python's actual values. Print a split list
or index it under a length guard; iteration over symbolic split lists is not
yet supported.

Literal-list/tuple loops and `range(N)` with a literal integer are unrolled.
They are suitable only when the task really has that finite bound. Quantified
iteration is supported for trusted `list[str]` results such as `find` and `grep`,
and trusted row-query results, including generic queries instantiated with a
Row class. Use `for path in find(...)` and `for line in grep(...)`; assignment
of a trusted result to a name before iteration also works. Do not silently
replace “all files” with `range(50)`:
that truncates the task and may create many expensive obligations.

Inside symbolic loops, do not reassign outer variables, mutate containers, or
use break/continue/return. Names bound inside a loop are unavailable afterward.
Trusted string-list results are read-only: do not mutate them or pass the list
itself to another trusted effect. Pass individual strings instead. Fixed count
guarantees require a proven list-length bound; do not invent a cap for an
unknown-length result.

Iterate the original trusted result or a direct name alias. Do not put returned
lists inside other containers or use them as dictionary defaults. Copies are
currently treated as read-only too. Use a fresh loop-target name: symbolic
loops cannot reuse an existing parameter or an earlier loop's target.

The directory-search recipe composes find and grep for UTF-8 text files. Binary
or unreadable files can still abort execution: `try`/`except` is unsupported.
Report skipped paths and partial results; do not claim a complete search after
an error. A returned path does not inherit a scope guarantee from find's input:
guard the actual path before reading it. The stronger `".." not in path` guard
proves the parent-segment precondition efficiently but also skips harmless names
containing two dots; the recipe reports those skips explicitly.

For database creation and insertion, use `create_table` and `insert_row`
from the database effects module; see the `create-duckdb` recipe. Construct
Row values with keyword fields of the declared `str`, `int` or `bool` type.
Use database write contracts, since filesystem guarantees do not cover them.
Lists of constructed Row values are not supported; insert rows individually.

## Discovery details

`tools(query=...)` searches the public API view. It lists callable effects,
registered contract helpers, Row types and preconditions without importing code.
Use `tools(query=..., view="source")` only when implementation detail is useful;
its imports, constants and helper functions are not additional agent tools.
Read recipe documents separately through `list_skills` and `read_skill`.
