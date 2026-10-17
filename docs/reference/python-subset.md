# ClauZ3 Python Subset

## Status

This is the current implementation spec for the Python subset used by
`clauz3` / `clauz3`.

The name "PyZ3" is tempting, but it is easy to confuse with Z3's own Python API.
This document uses **ClauZ3 Python subset** for the source language and **Z3
encoding** for the target constraints.

The implementation has two related subsets:

1. **Program subset**: agent-authored Python and trusted precondition lambdas
   that are symbolically evaluated by the vendored `deal-solver` machinery.
2. **Effect-spec subset**: relation lambdas inside `clauz3.spec`, such as
   `Email.all(lambda e: e.addr in allowed)`.

The first subset is broader and Python-like. The second is intentionally smaller
and closer to relational algebra.

## Execution Model

`clauz3 prove` takes one or more untrusted entry files, each proved on its own
with the import roots it would get alone, plus zero or more trusted roots.
Trusted roots contain ordinary Python modules for both effect functions and
contract logic.

```bash
cd examples/email
uv run clauz3 prove \
  --trusted-root tools/email/trusted \
  cases/only_bob_pass.py
```

The current loader treats the entry file and trusted roots differently:

- the entry file is parsed as the untrusted program being proved
- trusted modules below `--trusted-root` are imported under their normal Python
  module names so `@contract` helpers register themselves
- import roots locate trusted modules; nontrusted imports must also resolve to
  authorized library origins

The prover symbolically executes the selected target function, defaulting to
`main`. Values are represented by proxy objects backed by Z3 expressions. A
proof succeeds when Z3 cannot find a counterexample to the generated
constraints.

The selected target must have an unambiguous module binding. `prove` and `run`
refuse duplicate definitions or a target rebound by an assignment, import,
class, or other module-level binding. A selected target must be a synchronous
function defined directly at module level, not inside a conditional statement.
The check also applies to `prove_text`; with `target=None`, it checks every
module-level function candidate. Duplicate helpers remain allowed when they
are not selected as targets. Module-level wildcard imports are refused because
their bindings cannot be determined from the entry source alone.

The approval request includes only the selected module-level function's
guarantees. Guarantees on a same-named nested function or method do not describe
the entry, though their grammar is still checked. Together with the binding
check, this keeps theorem selection, approval details, and execution pointing
to the same definition. In addition, `prove_path` and `clauz3 prove` run the
interpreter's module-level checks before proving the function body. The raw
`prove_text` API checks target bindings but does not run that module-level pass.

When the command is not run from the package root used by the entry file's
imports, pass `--import-root`:

```bash
uv run clauz3 prove \
  examples/email/cases/only_bob_pass.py \
  --trusted-root examples/email/tools/email/trusted \
  --import-root examples/email
```

Real programs can combine multiple trusted domains. Repeat `--trusted-root` or
use the plural form:

```bash
uv run clauz3 prove plan.py \
  --trusted-roots tools/email/trusted tools/db/trusted
```

The same pattern exists for import roots: repeat `--import-root` or use
`--import-roots`.

### Import allowlist

The untrusted entry file is checked against a positive import allowlist
before the prover runs. This closes the obvious bypass — an agent writing
`import os; os.system(...)` and reaching the executor without declaring any
trusted effect at all. The same check runs on the `clauz3 run` path, so the
gate cannot be skipped by switching subcommands.

Allowed by default:

- `clauz3`, `deal`, and `__future__`
- the trusted-layer prefixes derived from `--trusted-root` and
  `--import-root` (e.g. `tools.email.trusted` and its submodules; sibling
  paths under `tools.email` are *not* allowed)
- a small set of pure / functional stdlib modules with no I/O, no time
  access, and no non-determinism: `collections`, `dataclasses`, `decimal`,
  `enum`, `fractions`, `functools`, `itertools`, `json`, `math`, `re`,
  `statistics`, `string`, `typing`

Anything that reads the world or executes code — `os`, `sys`, `subprocess`,
`socket`, `pathlib`, `urllib`, `requests`, `pickle`, `marshal`, `tempfile`,
`time`, `random`, `datetime`, `multiprocessing`, … — is rejected. The
expected route for those capabilities is to wrap them in a trusted effect
under a `tools/<domain>/trusted/` root, where their preconditions become
proof obligations.

Operators can opt extra module names in with
`--allow-import MODULE` (repeatable). A bare name like `--allow-import
pickle` adds a top-level root; a dotted form like
`--allow-import tools.legacy.helpers` adds only that prefix without
unlocking siblings. This grants permission to use the name, not to execute
local source or expand the interpreter's supported surface. Relative imports
(`from . import sibling`) are always rejected in untrusted source.

The allowlist applies only to untrusted source. Trusted effects under
`--trusted-root` can use ordinary Python, including real I/O, networking, and
stdlib dependencies. Their imported objects still undergo source and identity
validation, subject to the configurable
[dependency work limits](#trusted-dependency-work-limits).

Allowlisting a module does not expose every name it contains. Both `clauz3
prove` and the `clauz3 run` preflight check module-level imports against the
interpreter's supported surface. For example, `from math import sin` is
supported, while `from typing import Final` is refused with exit 2 because
`typing` exposes no
runtime values. `import typing` can still supply qualified annotation syntax;
annotations are read as syntax rather than evaluated. Missing allowlisted
modules also produce an exit-2 configuration error. `--allow-import` changes
the import policy, not the interpreter's supported values.

The whole-source import allowlist and nontrusted origins are checked before
loading trusted modules, including imports inside unused function bodies.
Nontrusted imports must resolve within the framework's captured standard-library
and installed-package roots, or its own package directory. Every parent package
and namespace portion is checked, along with any cached module's origin and
package paths. A local `statistics.py` beside the entry is refused even though
`statistics` is allowlisted. Adding `--import-root` or `--allow-import` does not
authorize such a file.
One process can alternate between trusted projects that share module names.
Each operation selects verified Python modules, Astroid source trees, and
contract/model registrations for its configured roots. An unchanged project
reuses its module objects and does not rerun their initializers. This preserves
the `prove_text` → inspect or patch a verified provider → `execute` workflow.
`prove_path` uses explicitly supplied import roots in their given order, matching
the other APIs. When none are supplied, it defaults to the working directory and
the entry file's parent directory.

When a request displaces an existing module binding, the old binding and parent
package link are restored afterward, including after failure. Newly imported,
verified modules with no conflicting binding remain available to the host.
New children of a displaced package stay with their selected project; they are
not attached to the restored host package.
After selecting project B over an ambient project A, an ordinary host import
again sees A; retain B's module reference or inspect the operation's outcome
when observing B. This does not roll back module globals or trusted effects.

A shared source filename alone does not make a cached module reusable. Its
retained imports must agree with the selected dependency sources, including
transitive helpers and copied import bindings. Imported functions must retain
the selected module's actual globals; a matching module name or filename alone
does not establish that identity. Named imports must match the selected
module's exported objects. When imports or aliases replace one callable with
another, reuse requires its binding history from checked initialization.
An earlier matching import does not establish the final binding of a preloaded
module. Decorated providers retain their verified wrapper chains; cyclic or
unsupported chains are refused. A first-use cached dependency
that retains another project's state is refused before proof or approval.
Bytecode-only Python dependencies outside the captured engine are refused
because their copied imports cannot be checked against readable source.
Outside that engine, modules using `from ... import *` must first initialize
within the selected layer. Their imported bindings are recorded for later
reuse; preloaded modules with unknown star-import history are refused.
Verified dependencies from bundled tool layers retain their identities with
the selected project, including helpers outside its configured trusted roots.
Retaining a helper does not make it an authorized trusted effect boundary.
Initialized framework and standard-library dependencies retain their verified
engine identities and source plans; project imports cannot redirect them.
CLI and direct prover entry points initialize the same explicit stdlib
dependencies (`argparse`, `asyncio`, `decimal`, `secrets`, `http.server`,
`urllib.request`, and `subprocess`) before capturing those identities. Eligible
stdlib modules that a Python host loaded earlier can also appear in the
snapshot. Preloading an arbitrary installed package does not grant it engine
authority; its retained project dependencies still undergo validation.

Successful layer operations retain verified star-import and callable-alias
history for reuse by another layer holding the same dependency module object.
Reuse rechecks the source bytes and import selections of the inspected
dependencies, as well as their current exports and retained objects. Failed
operations do not publish this history. A module loaded by a host without
verified history cannot acquire it merely because its name or path matches.
These rules concern imports inside trusted tool implementations; they do not
permit agent-authored code to import additional modules such as `asyncio`.

Trusted source inventory, import selection, and known local dependencies are
fingerprinted. Changed files or namespace selections refuse reuse and require
a new process; the loader does not silently reload classes or discard provider
patches. The same check runs after approval and before execution. The supported
Astroid cache layout is checked explicitly; an unknown layout is refused.

Proof and runtime operations share a lock while their layer is active. Approval
HTTP requests release it, and execution resumes the exact selected layer,
including lazy trusted imports. Nested use of the same layer is supported;
incompatible nested selection refuses. A competing operation waits up to five
seconds before a configuration refusal, so a trusted call must not rely on a
synchronous callback in another thread entering the same process. Use separate
processes for parallel long-running trusted operations.

Neither proof, preflight, nor execution initializes a nontrusted module selected
by the entry. The module check binds inert names. At execution, supported
library calls use fixed providers captured during framework initialization;
modules with no supported runtime values remain inert. Importing a genuine
`statistics` module therefore cannot initialize an entry-supplied `numbers.py`
dependency. Discovery and binding do not read dynamic module attributes or
execute parent initializers.

`@clauz3.solver` registers a symbolic model; it does not install a runtime
library provider. If a registered library name has no captured implementation,
an unused import of that library remains inert. Binding or reading the missing
member anywhere in the entry source is refused before proof or approval,
including inside unused helpers. Saving or forwarding a library namespace with
such missing members is conservatively refused too; supported members remain
available through direct attribute access. Registrations are selected with the
verified layer, so an earlier request's custom model cannot donate authority to
another project. The runtime provider surface is not extensible through this
decorator.

A trusted dotted-name prefix is not sufficient either: imports throughout the
source must match modules already loaded from files under the configured trusted
roots, or inert namespace directories inside those roots. These checks precede
entry effects and run approval. Explicitly trusted modules still initialize
their own dependencies normally under the checked trusted-layer context.
Before loading any trusted file, its complete import chain is checked against
the configured files and cached package paths. Ancestor packages may be
namespaces; an executable parent `__init__.py` must itself lie inside an
explicit trusted root. An empty initializer outside those roots is also refused.

**Upgrading an existing trusted package:** ancestor `__init__.py` files that
previously imported successfully now cause a configuration refusal when they
lie outside the configured trusted root. For `tools/email/trusted`, remove
`tools/__init__.py` and `tools/email/__init__.py` if they serve only as package
markers; namespace ancestors need neither file. Keep the trusted root at
`tools/email/trusted`, including any initializer inside it. An ancestor that
must execute initialization code needs a separately reviewed, explicit trust
decision; broadening the root merely to bypass this check is not a migration.

Nontrusted import discovery supports filesystem modules and packages, namespace
packages, and Python's built-in and frozen modules. It does not run custom
import hooks or load modules from zip archives. A nontrusted name discoverable
only through those mechanisms is refused during the declaration check. Trusted
chains are planned with the same filesystem checks, then loaded through normal
`importlib` machinery, including hooks configured by the embedding host. Trusted
layouts relying on custom finders or zip archives are not supported; the trusted
loading path does not provide hook isolation from the host environment.
Library modules must resolve within the captured environment installation
directories; the clauz3 package's own source directory is also authorized.
Adding another dependency's source directory through a `.pth` file does not
authorize it. Install such dependencies in the environment's installation
directories. clauz3 itself can use a filesystem-based editable installation;
editable modes requiring a custom finder are not supported.

Use `import package.module as alias` or `from package import module` for dotted
modules. Unaliased `import package.module`, relative imports, and wildcard
imports are refused. The raw `prove_text` API enforces the import allowlist but
does not load module-level imports through the interpreter; its result alone
does not certify that a whole entry file is runnable.

For Python API callers, `run_source` normalizes source and configuration refusals
to `RunConfigError`, including `ProverConfigError` raised by trusted-layer loading
or proof setup (such as invalid condition signatures, relations, or guarantees).
Callers that previously caught `ProverConfigError` around `run_source` should
catch `RunConfigError` instead. `prove_path` retains `ProverConfigError`.
Failed proof conclusions remain `RunProofError`; modeled program faults during
approved execution remain `RunProgramError` rather than configuration errors.

### Trusted dependency work limits

Selecting a trusted layer checks its retained dependency graph, including
installed packages that retain project-local imports. Closure walks and the
walk selecting registration owners have bounded work:

| Process environment variable | Default | What it bounds |
| --- | --- | --- |
| `CLAUZ3_MAX_DEPENDENCY_MODULES` | `256` | Distinct module objects visited per retained closure or registration-selection walk. |
| `CLAUZ3_MAX_DEPENDENCY_AST_NODES` | `500000` | AST nodes inspected and star-import exports in one closure. |

A larger SDK or generated package can exceed these defaults. Set positive
integer limits in the environment of each process performing validation:

```bash
export CLAUZ3_MAX_DEPENDENCY_MODULES=1024
export CLAUZ3_MAX_DEPENDENCY_AST_NODES=2000000
```

These settings apply to the CLI and Python APIs, including the approval
service. Invalid settings are refused before selected modules initialize;
settings are read again for later operations. Approval requests cannot supply
these limits. Exceeding a limit raises a configuration refusal with the relevant
variable name, even if symbolic proving has already finished. Approval-policy
evaluation falls back to a human decision and reports the refusal diagnostic.
Validation never succeeds by truncating the graph. Raising a limit permits more
validation work; every dependency must still satisfy the same origin, source,
and retained-object checks.

## Program Subset

### Entry Points

Entry theorems are discovered from synchronous functions defined directly at
module level. The target function defaults to `main`. Function arguments must
have supported type annotations.

### Supported Type Annotations

Function argument annotations support:

- `bool`
- `int`
- `float`
- `str`
- `list[T]` / `typing.List[T]`
- `set[T]` / `typing.Set[T]`
- `dict[K, V]` / `typing.Dict[K, V]`
- variable-length `tuple[T, ...]`
- `Pattern`, as a symbolic value without usable interpreter attributes

`Pattern` annotations can still create symbolic pattern values, but those
values have no usable interpreter attribute surface. Pattern methods are
refused, and the separate regex library-call parity gap remains tracked in
[#122](https://github.com/cmungall/agent-deal/issues/122).

Selected aliases from `typing` are normalized:

- `Sequence[T]` and `Iterable[T]` as `list[T]`
- `Mapping[K, V]` and `MutableMapping[K, V]` as `dict[K, V]`
- `AnyStr` as `str`
- `FrozenSet[T]` as `set[T]`

`Sized` is also mapped internally, but it is not a recommended annotation for
new code because it does not describe an element type.

Unsupported or missing annotations make the theorem unprovable.

### Supported Statements

At module scope, `clauz3 prove` and `clauz3 run` use the same interpreter pass.
It admits supported imports, function definitions with literal positional defaults,
`clauz3.Row` schemas, literal assignments (including annotated assignments and
tuple/list destructuring into names), `pass`, and docstrings. Signed numeric
literals and containers of literals are allowed. Function decorators are limited
to `@clauz3.guarantee`. Calls, computed assignment values, and assignment targets
that evaluate expressions are refused with exit 2 before proving. Imported
trusted modules can run their initialization while this pass loads them; the
entry's functions are not called.

Approval-policy evaluation runs this check before considering any rule,
including one with no clauses. A refusal leaves the decision to a human. The
raw `prove_text` API remains a function-body proof primitive and does not run
this pass. The pass also does not enter function bodies: an effectful default
on a nested function remains a separate prove/run mismatch, refused by the
interpreter when that definition is reached.

Inside functions, the current evaluator supports:

- `assert expr`
- expression statements
- assignment to a local name: `x = expr`
- assignment to an indexed container item: `xs[i] = value`
- augmented assignment to a local name already bound in the current function
  (an earlier assignment or a parameter) whose value is a `bool`, `int`,
  `float` or `str`: `x += expr` is desugared to `x = x + expr`, for all twelve
  operators (`+= -= *= /= //= %= **= &= |= ^= <<= >>=`). `/=` turns an `int`
  into a `float`, as in Python. Inside a loop over a query result it is a
  rebinding of a name bound outside the loop, so it is refused with the other
  loop-carried state described below; in an unrolled loop it accumulates
  normally
- `return expr`
- `if expr: ... else: ...`
- `for` loops over literal lists/tuples, `range(N)` with a literal integer, or
  trusted `list[str]`, `list[Row]`, or generic Row `list[T]` query returns. A loop over a query
  result is quantified rather than unrolled, so its body must be a function of
  the current row: it may not assign to the loop variable or to any name bound
  outside the loop, mutate containers through methods, or assign container
  items. A name it does bind is not readable after the loop. Loops over literal
  sequences and `range(N)` are unrolled; these restrictions do not apply when
  they run outside a quantified loop body.
- `raise SomeException`
- nested function definitions
- `import`, `from ... import ...`, `global`, and `pass` as no-ops

Trusted string-list results may be assigned to a name and iterated, including
in nested loops. Arbitrary computed lists, other scalar element types, and
container mutations inside symbolic loops are not supported. A trusted
string-list result is read-only in this subset: it cannot be mutated or passed
as a container argument to another trusted effect (including inside another
container), because that effect could mutate an aliased iteration source.
Do not place trusted string-list results inside another list, tuple, dictionary,
or set, or use them as dictionary defaults. Those operations can lose alias
information, so the prover refuses them. Keep the original result or a direct
name alias for iteration.
The current model also keeps `.copy()` and `list(result)` read-only.

Symbolic loop bodies must prove exception-free, even when a direct prover
caller declares an allowed exception. Exceptions in an empty loop do not
interrupt subsequent statements. A loop target cannot replace an existing
binding, and body-local bindings are not available after a symbolic loop.
Nested trusted results are distinct symbolic values for each outer iteration.
Count guarantees need provable length bounds; an unknown-length list cannot
promise an arbitrary fixed cap. Counts over dependent nested loops are
conservative bounds, not exact summations.

The container-mutation restriction applies to all symbolic loops, including
Row queries: list, set, and dictionary updates are refused even for an empty
query. Literal-list and literal-range loops retain their existing behavior.
Z3 can return an inconclusive result for quantified string-list guarantees,
including false universal claims over unconstrained elements. Such a result
does not refute or establish the guarantee and cannot reach approval.

Container mutation in a quantified loop produces `SKIP`, including mutations
through bound method aliases, branches, called helpers, and nested unrolled
loops. The refusal also covers containers created inside the body or a helper:
the current model does not distinguish those from shared containers whose
updates it would lose. Construct values with pure expressions instead, such as
`[row.id]`, `[] + [row.id]`, or `{row.id: 1}`. Pure reads and modelled trusted
effects remain supported. Evaluating the loop's iterable and statements after
the loop are outside this restriction, unless they are themselves inside an
enclosing quantified loop body.

`if` statements are symbolic. The evaluator executes both branches and merges
variables, assumptions, assertions, returns, exceptions, and trusted-call facts
under the branch condition. So a trusted call made inside a branch keeps its
postcondition on that path — a bounded query proves the same whether or not it
sits under an `if` — while that assumption says nothing about the path the
branch skipped.

Unsupported statements include:

- `while`
- `for` loop shapes other than the supported bounded/query forms above
- `with`
- `try` / `except` / `finally`
- `async` / `await`
- `match`
- `del`
- augmented assignment to an unbound name (including a module-level constant
  such as `LIMIT += 1` inside a function, which is an `UnboundLocalError` in
  Python), to an attribute, or to a subscript (`xs[i] += 1`)
- augmented assignment to a `list`, `set` or `dict`: these mutate in place, so
  rewriting `xs += ys` to `xs = xs + ys` would be unsound under aliasing
- augmented assignment to a `tuple`. Python's tuple is immutable, so the
  rewrite would be sound in Python; it is refused because in the solver's sort
  model a list is a kind of tuple (`ListSort` subclasses `VarTupleSort`), and
  admitting the tuple sort would admit lists with it
- augmented assignment at module level, which `clauz3 run` refuses before
  approval like any other non-declaration statement
- annotated assignment inside a function, such as `x: int = 1` (literal
  annotated assignments at module scope are admitted)
- slice assignment
- comprehensions other than list comprehensions

Unsupported statements should be treated as proof failures or partial proofs,
not as permission to run.

### Supported Expressions

The program subset supports:

- constants: `bool`, `int`, `float`, `str`
- names and local variables
- attributes
- calls
- lambdas
- conditional expressions: `a if cond else b`
- list, set, dict, and tuple literals
- one-generator list comprehensions, with optional `if` filters
- indexing: `x[i]`
- slicing without a step: `x[start:stop]`
- boolean expressions: `and`, `or`
- unary operators: `+`, `-`, `~`, `not`
- binary operators:
  - arithmetic: `+`, `-`, `*`, `/`, `//`, `**`, `%`
  - bitwise: `&`, `|`, `^`, `<<`, `>>`
- comparisons:
  - `<`, `<=`, `>`, `>=`, `==`, `!=`
  - `in`, `not in`
- chained comparisons (`0 < x <= 200`), which lower to the conjunction of
  pairwise comparisons matching Python semantics; intermediate expressions are
  evaluated once

Strings preserve Python's complete code-point range, U+0000 through U+10FFFF,
including NUL, lone surrogates, and literal backslashes. This applies to
literals, annotated inputs, trusted returns, nested containers, Row/query
fields, contract arguments, fluent keys/values, and regular-expression
literals. A raw Python string such as `r"\u{41}"` keeps its six characters;
it is not interpreted as Z3 escape syntax. `len` counts Python characters,
and `ord` returns their full code points.

Internally, strings use sequences of 21-bit code points, with constraints that
each character is at most U+10FFFF. The prover applies these constraints to
external symbolic values, including nested strings; it does not narrow the
runtime string domain. Custom solver models must use the shared string
constructors rather than native Z3 `StringSort`/`StringVal` values.

When Z3 cannot decide an obligation involving nested sequences, the prover
also searches for a small concrete counterexample under the original
constraints. This incomplete search tries small sequence shapes with lengths
up to four and a budget of at most one second. A satisfying model reports
`failed` with the counterexample; exhausting the search leaves the obligation
undecided and never establishes a successful proof.

List-comprehension elements and filters must not mutate containers. For
example, `[values.append(x) for x in inputs]` is refused: the comprehension's
symbolic child context cannot preserve those updates in the surrounding
function. The refusal also applies through bound-method aliases, nested
comprehensions, and helper calls. It conservatively includes mutations of
containers created inside those helpers. Use pure expressions to build the
helper's result, such as list literals or concatenation, instead of mutating a
temporary container. Pure operations such as arithmetic and `len` remain
supported. Existing restrictions on trusted effects, preconditions, and
exceptions inside comprehensions still apply.

The outer iterable expression is evaluated in the surrounding function before
this restriction begins. This comprehension-specific restriction does not
apply to mutations there or after a comprehension completes; those calls still
have to satisfy the surrounding function's proof obligations and supported
subset. An inner comprehension's iterable is still part of the enclosing
comprehension and inherits its restriction.

Attribute access evaluates its receiver normally. If a call in `helper().ok`
or `probe(...).method()` cannot be modelled, the proof is refused; the prover
does not infer a replacement return value from the function's body. This
preserves failures from unsupported statements, declarations, and trusted
return annotations, and stops `clauz3 run` before approval or entry execution.

Static resolution is reserved for supported callable attributes on imported
module paths, such as `math.sin`. Every component of the receiver path must
come from an explicit import or actual submodule, without a conflicting local
binding. Computed module aliases and unresolved re-export chains are refused.
All inferred callable definitions must share the same registered model.

Path joins use separate models: `posixpath.join` supports symbolic string
components and follows POSIX rules for absolute components, empty components,
and existing separators. `ntpath.join` supports strings that reduce to concrete
values, using Windows drive, UNC, and separator semantics on every host;
symbolic Windows paths are refused. Neither model normalizes the joined path.
Both currently accept positional string arguments only.

`os.path.join`, including import aliases, is refused because its platform is
implicit or ambiguous during static resolution. Use `posixpath.join` or
`ntpath.join` to state the intended flavour explicitly. These modules require
the corresponding `--allow-import` opt-in for untrusted entry code.

Module data reads are refused, including scalar literals, signed numbers,
deeply immutable tuples, and library constants such as `math.pi`. Immutable
values and `Final` annotations do not make a module binding permanently
immutable: an initializer, another module, or a later trusted call can replace
it while static inference still sees its declaration. Comparing a live value
once cannot establish stability across later calls or an approval wait. This
refusal stops `clauz3 run` before approval and entry execution, even for bindings
that happen to stay unchanged. Module aliases, re-export paths, and ordinary
helper calls do not bypass it; bare imported data names remain unsupported.
Local literals and containers evaluated within the function retain their
existing support.

Computed declarations such as `VALUE = build_value()` or `ALLOWED = (allowed,)`
are also refused rather than evaluated using the caller's local variables.
Use local literals for fixed configuration and expose changing state through
modelled trusted returns instead of module data reads.

Bare callable names must resolve to one definition. Direct imports, import
aliases, and explicit re-exports are supported; conflicting or unknown targets,
duplicate aliases in one import, and computed callable bindings are refused.
Simple name aliases preserve the same checks through their import chain.
For a module-level alias, use `from re import match as f`; an assignment such
as `f = re.match` is outside this static binding grammar. Local aliases already
evaluated by the prover retain their symbolic binding.
Builtin models follow the resolved definition: a shadowed or unresolved name
such as `len` cannot select the builtin model merely by its spelling.

Value attributes must belong to the interpreter's supported method/property
surface or name a declared Row instance field. Merely saving an unsupported
attribute, such as `saved = text.upper`, is refused even if it is never called.
This includes explicit `__call__` wrappers on functions, lambdas, methods, Row
constructors, and noncallable values. The proof refuses these accesses before
approval or any earlier effect can run. Module-path resolution is unchanged.

Call functions and supported methods directly. Local function aliases and
pure bound-method aliases on immutable scalars or tuples of immutable values
remain supported. Methods on lists, dictionaries, and sets must be called
immediately: saving even a pure query such as `snapshot = items.copy` would
retain a stale symbolic receiver when the real collection later changes.

Mutating methods and item assignments additionally require a simple receiver
name bound in the scope containing the mutation, whose bindings do not depend
on function or lambda parameters. Direct mutation of a fresh local collection
remains supported. Saving or passing `items.append`, calling
a mutator on `items.copy()`, or mutating a parameter through a local alias is
refused. A lambda or comprehension cannot mutate a collection captured from
its enclosing function, including through an alias or an iteration variable. The syntactic binding
check also conservatively refuses a captured receiver in a comprehension's
outer iterable, although Python evaluates that iterable in the caller.
Item assignment to a computed receiver, such as `items.copy()['key'] = value`,
is also refused. The binding check follows all lexical assignments conservatively,
including conditional aliases and values returned by helpers; copies and
reassigned parameter names can therefore also be refused. Cyclic, unsupported,
or oversized binding dependencies are refused. These restrictions stop `run`
before approval or execution.

Boolean operands, conditional-expression arms, and chained comparisons now
use guarded evaluators: skipped expressions do not contribute effects,
exceptions, or obligations. Trusted calls can be modeled under those guards;
mutations remain refused in evaluated non-first Boolean operands and guarded expressions.

Collection writes also require a uniquely owned local value. A fresh list,
dictionary, or set can be mutated through its owning name. Creating another
binding to it, retaining it in another container, or passing it to an ordinary
helper makes it shared: subsequent writes through either name are refused.
This includes iteration aliases such as `for item in [values]`.
Read-only aliases remain supported. Rebinding an alias does not restore write
permission to the original collection.

Known fresh outer collections, such as flat copies, supported constructors,
slices, and concatenations, have independent ownership even when their values
are equal. Shallow copies do not make nested collections independent. Mutable
values extracted from containers or returned by ordinary helpers are treated
as borrowed and cannot be mutated. Unknown provenance and ambiguous ownership
at branch joins are refused before a write. Existing restrictions on captured
receivers, parameters, and quantified-loop mutations still apply.

Trusted calls retain the interpreter's existing copy boundary: mutable
arguments and returned containers are copied when crossing it, so a trusted
function cannot change the interpreter's argument object. Ordinary helpers
execute within the interpreter and do share their arguments. These ownership
checks preserve read-only query and Row processing without modeling a shared
mutable heap.

Nested helpers can use module globals and their own arguments. For a nested
`def`, names captured from an enclosing function have a narrower static grammar:
an import, nested helper definition, or simple callable-name alias must have one
unconditional binding, established before the nested scope is defined. Alias
chains must satisfy the same rule. Reassigned, conditional, loop-dependent,
parameter, and late-bound captures are refused with a `closure binding` diagnostic instead of
inferring a value from a different point in execution. This also applies to
captured module aliases such as `ops.VALUE`.

The prover does not model general closure cells for nested `def` helpers. A later
reassignment makes a capture unsupported even when a particular call precedes it.
Pass changing values as explicit helper arguments. Captured scalar values
remain outside the static callable-binding grammar; this rule does not add
support for them. Module helpers retain their module's lexical bindings and
do not read same-named locals from their caller.

Lambdas use a separate evaluator. A lambda that captures an enclosing local can
be called only from the symbolic scope where it was created. Calling it inside
a child branch or through another helper is refused with a `lambda capture`
diagnostic, even when the capture is constant or the branch is unreachable.
Returned captured lambdas and captures used by nested lambda bodies follow the
same restriction. The prover does not model shared closure cells across these
scopes ([#200](https://github.com/cmungall/agent-deal/issues/200)).

Calls in the defining scope, including after branch merging or in a literal
loop that reuses that scope, continue to read its current values. Lambdas without
enclosing local captures can cross scopes, subject to the ordinary name-resolution
rules above; this does not add support for bare module-level constants. Pass
changing local values as explicit positional arguments instead of capturing them.

Lambda calls require exactly one positional value per parameter; positional-only
parameters are supported. Missing or extra values and keyword calls are refused
with an `unsupported lambda argument binding` diagnostic. Declarations with
defaults, keyword-only parameters, `*args`, or `**kwargs` are refused when created,
even if never called: defaults can execute effects at definition time. These
lambda-specific restrictions also apply when ordinary and registered functions
support a broader argument-binding form.

Modeled mutating methods, such as list `append`, are refused while evaluating a
lambda body, including through helpers, comprehensions, and bound method aliases.
The lambda evaluator does not propagate these state changes back to its caller.
Pure operations such as `len(values)` remain supported, subject to the existing
restrictions on possible exceptions. Perform mutations as ordinary statements
outside lambdas.

`and` and `or` evaluate operands from left to right and return the selected
operand's value. A statically determined short circuit skips the remaining
operands. When selection is symbolic, effects, preconditions, postconditions,
and exceptions from a later operand are conditioned on that operand executing.

Supported operands are Boolean, integer, floating-point, and string values.
Statically selected results can have different scalar types; symbolic choices
must have matching represented scalar types or produce `SKIP`. Collection,
callable, Row, and query values as operands are refused. Mutations inside a
right-hand operand, including through helpers, aliases, and lambdas, are also
refused; the conditional evaluator does not implement mutable object identity.
Pure construction used to produce a scalar, such as `{'x': 1}['x']`, remains
supported. These value-selection rules also apply to relation lambdas, which
continue to be compiled symbolically.

Conditional expressions and chained comparisons also follow Python's
conditional evaluation order. Statically skipped arms and later comparison
steps are not evaluated. On symbolic paths, assumptions, assertions,
exceptions, and trusted effects are guarded by the condition that reaches
them. Each comparison operation runs under that guard too. Mutations within
an evaluated conditional arm or comparison step are refused, including
mutations through helpers and lambdas. Conditional expressions have the same
scalar-result restrictions as Boolean expressions, and additionally support
`None` results when both arms return `None`.

Integer bitwise operators are supported only when every operand reduces to a
concrete integer. Operands and results are limited to a magnitude of 4,096 bits,
and shift counts are limited to the inclusive range 0 through 4,096. Negative
shifts and expressions above the safety limits are unsupported and therefore
fail closed rather than recording a suppressible exception. Concrete reduction is bounded
to 1,024 distinct expression nodes and 128 nesting levels, and every integer
intermediate has the same 4,096-bit limit. The reducer recognizes literals,
unary negation, `+`, `-`, `*`, `//`, `%`, comparisons, boolean combinations,
and concrete conditionals; other otherwise-concrete forms such as `**` fail
closed when used as bitwise operands. Integer literals wider than 100,000 bits
are rejected before constructing their Z3 representation.

Binary bitwise operators accept concrete `int` and `bool` operands. Booleans
are coerced to `0` or `1`, matching Python: `&`, `|`, and `^` return a boolean
when both operands are booleans and an integer otherwise, while `<<` and `>>`
return an integer. Symbolic boolean-only `&`, `|`, and `^` are modeled exactly;
symbolic shifts and mixed boolean/integer bitwise expressions fail closed under
the same concrete-only policy as symbolic integers. Unary `~` on a concrete boolean is
converted to its integer value and follows the same limits.

#### Numeric semantics

Supported floating-point operations use IEEE-754 binary64 with
round-to-nearest-even, matching CPython's built-in `float`. Integer true
division rounds the exact integer ratio directly to binary64; it does not
pre-convert each operand. Mixed integer/float arithmetic performs Python's
checked integer-to-float conversion, while mixed comparisons compare exact
numeric values without rounding the integer.

`int(float_value)` truncates toward zero and reports Python's `ValueError` for
NaN or `OverflowError` for infinity. Float floor division and modulo are
derived together using Python's quotient correction and divisor-sign rules;
division, floor division, and modulo by either sign of zero report
`ZeroDivisionError`. Integer true division also reports division by zero and a
result that overflows binary64.

Converting an integer to a float is checked, so an *unbounded* symbolic integer
carries a real `OverflowError` obligation. A `main(n: int)` parameter, a trusted
`-> int` return, and an `int` Row column are all unbounded, so `float(n)`,
`n / 2.0` and `n * 1.5` need a proven bound on `n`. A
`@clauz3.requires(...)` condition bounds parameters when proving that function;
on a called helper, its requirement is an obligation for the caller to prove.
The same applies to a comprehension element, whose value is symbolic:
`[x // 2 for x in ...]` proves, while `[x / 2 for x in ...]` fails closed. This is Python's
behaviour: for example, `float(10**400)` raises in CPython.

`math.trunc` accepts modeled integers, booleans, and floats. Other argument
types fail closed; Python's `math.trunc` does not parse numeric strings.

Query results use their list length for truth tests, and sets are true exactly
when nonempty. These truth tests work in `if` statements, `not`, `bool()`, and
conditional expressions. A proxy without an explicit truth model fails closed
instead of assuming it is true. Boolean `and`/`or` operands and conditional
expression results retain their separate restriction to immutable scalars.

### Calls

Supported calls include:

- calls to local functions in the proved source
- calls to trusted functions imported from trusted roots
- calls to registered domain contract functions when evaluating guarantees
- selected builtins
- selected methods on supported proxy types

Positional and keyword arguments are supported. Trusted calls bind to their
declared positional-only, ordinary, and keyword-only parameters before
recording an effect; their runtime implementations use Python's binding.
Ordinary source-defined helpers support only positional-or-keyword parameters.
The prover refuses calls to helpers with positional-only or keyword-only
parameters, matching the interpreter's restrictions before approval or execution.
For these source-defined functions, missing required arguments, excess positional
arguments, duplicate or unknown keywords, and positional-only parameters passed
by keyword are refused. This does not cover the separate lambda evaluator.
Variadic declarations and `*args`/`**kwargs` call unpacking are unsupported.

Trusted facts and query-source metadata record only explicitly supplied
arguments. An omitted trusted default remains absent, so a guarantee cannot
treat omission as explicit intent. Ordinary helper calls must supply every
parameter, including defaulted ones: evaluating a default expression at call
time would not reproduce Python's definition-time semantics.

The keyword names `self`, `ctx`, and `var_name` are reserved by the current proof
dispatcher and are refused before dispatch. A parameter with one of these names
can still receive a positional argument.

Registered library models validate an explicit supported call signature before
execution. Unknown keywords, incorrect arity, and unmodeled options produce
`SKIP`; an option is never silently discarded. Unsupported forms include
`sum(..., start=...)`, `min`/`max` options or more than two positional operands,
conversion base/encoding options, dictionary keyword fields, print options,
`randrange` step, regular-expression flags, and path-join keywords. Conversion
models currently require one operand. Supported options, including
`math.isclose` tolerances and regular-expression pattern/string keywords, keep
their argument values. This validation does not add new interpreter library
surfaces.

Registered `@contract` helpers automatically use the decorated function's
signature, preserving its keywords and defaults. Lower-level custom models
registered with `clauz3.solver(name, signature=...)` must supply a callable
describing their supported public signature or an `inspect.Signature` object.
The signature excludes the solver's internal `ctx` parameter. A model with no
signature metadata is refused when called during proof.

User-written `.__call__` wrappers on helpers are outside the interpreter's
supported attribute surface. The prover refuses them before approval or execution; use direct calls.

Supported builtins currently include:

- `print(...)`, treated as a no-op
- `sum(...)`
- `min(...)` and `max(...)`
- `ord(...)`
- `abs(...)`
- `len(...)`
- `int(...)`, `float(...)`, `str(...)`, `bool(...)`
- `set()`, `list()`, and `dict()` with no argument or an already-compatible
  proxy value

`int(value)` accepts exactly one positional argument; explicit bases, keyword
arguments, and argument unpacking are refused. Concrete strings use Python's
decimal conversion, including signs, surrounding whitespace, underscores,
and Unicode decimal digits. Concrete concatenations are supported too. Invalid
text and symbolic strings produce `SKIP` before approval; the prover never uses
an integer sentinel for a failed conversion. Python's configured decimal-digit
limit and the existing 100,000-bit integer-result limit remain in force. The
conversion does not change the process-wide digit limit. Numeric conversions
retain their existing models.

Call results preserve Python's `None`: trusted `-> None` effects, ordinary
helpers that fall through, and the `print()` no-op return a false-valued result
that is unequal to `False` or `0`. Void mutators such as `append`, `extend`,
`clear`, and set `add` return that same value while updating their receiver.
Value-returning mutators such as dictionary and set `pop` retain their returned
element and their receiver update.

These `None` results can be stored, forwarded to source-defined helpers, tested
with `if`, `not`, or `bool`, and compared for equality. Branches returning only
`None` can join; mixed `None`/value branches are unsupported. A helper with value
returns and an uncovered normal fallthrough path produces `SKIP`; modeled
exception paths do not count as normal fallthrough. Arithmetic, use as an index, attribute access,
and symbolic container storage involving `None` are refused when evaluated. Statically skipped Boolean operands and conditional
arms are not evaluated. Explicit `None` literals, bare
`return`, and `is None` remain outside the supported subset.

Boolean operators preserve scalar operand values and short-circuit evaluation.
`None` remains outside their supported operand types, so expressions such as
`quiet() or "x"` are refused instead of coercing the result to a Boolean.

Supported methods are type-specific and incomplete. Examples include:

- string containment via `"x" in s`
- `s.startswith(prefix)`
- `s.endswith(suffix)`
- `s.find(sub)` and `s.find(sub, start)`
- `s.index(sub)`
- list/tuple/set/dict containment where implemented
- indexing and `len(...)` for strings and sequence-like values

`str.count(substring)` accepts one positional string argument. Literal counts
use Python's exact nonoverlapping semantics, including empty substrings and
Unicode. Symbolic counts use a conservative deterministic model: they are
nonnegative, an empty substring occurs `len(text) + 1` times, and nonempty
counts are bounded by text length divided by substring length, with zero
exactly when the substring is absent. These constraints admit every actual
Python result but may also admit additional counts. A content-dependent claim
can therefore fail to prove even when true. Runtime counting uses Python's
exact implementation. Optional start/end arguments and keywords are refused.
`str.splitlines()` remains unsupported; use filesystem `read_lines` plus `len`
for logical line counts, including blank and final unterminated lines.

Differential replay currently samples the abstract count without requiring it
to equal Python's count of the sampled text. A count-dependent branch can
therefore produce a spurious `effect_mismatch` during replay. Treat such a
finding as requiring concrete validation; the conservative proof remains sound.
Replay needs to validate those counts or report an explicit abstraction
limitation before its effect comparison can be treated as an exact witness.

Regular-expression models preserve Unicode literals and character ranges
within the string alphabet above. Wildcards, negated literals, and prefix
matching use that full alphabet. Category escapes such as `\w`, `\d`, and
`\s`, and flags (including inline flags), are refused because their Python
semantics are not modeled. These models only support proof predicates: the
interpreter still refuses `re` calls whose `Match`/`Pattern` return values are
outside its value domain.

`str.index` accepts a substring and an optional integer or boolean start, both
positionally. Negative starts count from the end and are bounded below by zero;
positive starts are not reduced to the string's length. The prover supports a
search only when its local symbolic expression simplifies to a successful
result, without relying on caller preconditions or other assumptions. Missing
substrings and uncertain symbolic searches are skipped because Python raises
`ValueError` where Z3's search returns `-1`. Successful literal searches, and
symbolic searches that simplify to a known success, remain supported. End
bounds, keyword arguments, and other argument types are refused. This refusal
can also occur in a branch the prover visits even when Python would not execute
it. Statically skipped expressions are not visited; an exceptional result is
not modeled.

`str.find` accepts a string substring and an optional integer or Boolean start,
passed positionally. Negative starts count back from the string's end and are
clamped at zero; nonnegative starts are preserved, including starts beyond the
end. A missing match returns `-1`, as in Python. The optional end argument,
keyword arguments, and other argument types are refused at prove time.

Do not assume arbitrary Python library calls are supported.

## Declarations on a trusted function

A trusted stub (and a normal helper function) can carry:

- `@clauz3.trusted` — the side-effect boundary
- `@clauz3.requires(lambda ...)` — a precondition over the parameters
- `@clauz3.ensures(lambda result: ...)` — a postcondition over the return value
- `@clauz3.ensures(lambda arg, ..., result: ...)` — a postcondition that also
  reads the arguments, for a result that is only meaningful relative to what
  was asked for: `ensures(lambda cap, result: result <= cap)`. One of the
  parameters must be named `result`; the prover and the runtime check both
  bind the return value to that name.

The predicate has to be the lambda itself. The evaluator reads its *body*, so
a named function — `@clauz3.requires(small)` — is nothing it can interpret, and
the proof is refused rather than run without the condition. Silently ignoring
one meant a call the precondition forbade proved clean while deal went on
raising `PreContractError` at run time, which is the proof and the program
disagreeing about the same layer. Inline the lambda; the named callable is
still fine for deal's own runtime check.

Predicates evaluate with their own completion state: returning from the
function does not skip a live Boolean operand or hide its exception. Calls
that create trusted effect facts inside a condition predicate are refused;
condition evaluation cannot supply events for an effect guarantee.

Condition predicates must complete without a potentially live modeled exception
or an unchecked assertion/precondition from a nested helper. Such a predicate
produces `SKIP` before run approval or execution, for both inlined helpers and
trusted boundaries. The check uses the predicate's execution guard without
assuming its own result or a called function's postconditions. Exceptions must
simplify to unreachable and nested obligations to satisfied; otherwise the
predicate is refused even if additional caller assumptions might make it safe.
For example, a predicate that calls a helper with its own symbolic precondition
can be refused even when the caller's assumptions would establish that helper's
precondition.
Statically skipped Boolean operands stay skipped, and a false return guard
excludes that postcondition path's modeled exceptions and obligations.

A declared postcondition also requires a modeled return on every normal
completion path. Functions that fall through to implicit `None`, including
trusted effects annotated `-> None`, are refused when they declare a
postcondition, even if that predicate would accept `None`. Partial return paths
of functions with postconditions are refused too. Even without postconditions,
a helper whose value returns leave a normal fallthrough path is refused rather
than losing its possible `None` result.
This completion check uses only return and modeled exception
paths, without caller assumptions or contract propositions; a path that raises
does not run a postcondition. Fully covered scalar returns remain supported.
When simplification does not settle coverage, a solver check uses a
one-second budget capped by any remaining proof-session deadline. A timeout or unknown
result produces `SKIP` rather than assuming that every completion is covered.

The predicate must also declare the function's own parameters, with the same
names, kinds and defaults: `@clauz3.requires(lambda x, dry_run=True: x > 0)`
on `def f(x: int, dry_run: bool = True)`. deal calls the predicate with the
caller's arguments, and the prover reads its body in the function's parameter
scope, so a predicate that omits a defaulted parameter proves clean and then
raises `TypeError` at run time for the very call the default was there to
allow (issue #90). The argument-aware form of `ensures` follows the same rule
with a required `result` parameter added. A trailing positional `result` is
accepted only when the predicate has no `*args`; otherwise it must be
keyword-only. The result-only form, `ensures(lambda result: ...)`, instead
takes exactly one positional parameter and does not repeat the function's
parameters.

The check runs when the trusted layer is loaded and refuses the whole root
before any proof. It inspects non-class callables bound directly in module
globals, including imported and wrapped functions and package `__init__.py`
modules. It does not traverse methods, containers or factories; trusted
effects are declared as module-level functions. If the trusted directory is
also the import root, its bare `__init__.py` may contain only docstrings and
`pass`. To check functions in a package initializer, use the package's parent
as `--import-root`. This signature inspection does not make initializer
effects supported: the effect registry still excludes `__init__.py`. Declare
trusted effects in regular modules such as `effects.py` so relations, policy
triggers, and duplicate-name checks can discover them. Scanner consistency
is tracked in [#175](https://github.com/cmungall/agent-deal/issues/175).

Predicates whose sole parameter is named `_` are refused at proof time, even
`lambda _: True`, for both native and legacy declarations. At runtime this
spelling selects Deal's mapping calling convention; the prover does not model
that mapping or the predicate's separate lexical scope. Use explicit parameter
names as above. A parameter named `_` alongside other parameters does not
select the short form.

The load-time signature check still validates the mapping calling convention:
a short form must accept one positional mapping. Schema validators are
recognized only through the adapter active in Deal; ordinary functions and
partials always undergo signature comparison. Mapping
postconditions must also be able to bind the returned value to the function's
signature. Mapping preconditions and argument-aware postconditions cannot
use a function parameter named `result`, which Deal reserves. Passing these
load-time checks does not make short forms or schemas part of the supported
proof expression grammar.

A postcondition that *quantifies over a returned list* — every row the query
returns satisfies the `where` filter, every path the walk returns is under its
root — has no spelling: `all(... for x in result)` is not in the subset, and a
returned `list[str]` can be iterated in a program, but that does not introduce
a quantified postcondition syntax. Such a claim stays a docstring today,
believed because the trusted layer is audited. See
[docs/todos/quantified-postconditions.md](../todos/quantified-postconditions.md).

The prover also reads [deal](../how-to/deal.md)'s own decorators, which is what
the ClauZ3 ones are built on and what an older trusted layer states directly:
`@deal.pre`, `@deal.post`, `@deal.ensure`, `@deal.raises(SomeException)`,
and `@deal.has(...)` as a boundary when it
carries at least one marker. Prefer the ClauZ3 spelling: deal's own extractor
matches the literal text `deal.pre`, so an aliased `import deal as d` hid these
from it entirely, while ClauZ3 resolves a decorator through the module's import
bindings.

These supported legacy declarations require a decorator call. In particular,
`@deal.has()` asserts purity, while bare `@deal.has` is refused. Other Deal
decorators, including `safe`, `pure`, `inv`, `reason`, and `example`, are not
modelled and are refused on called functions.

For trusted-layer functions, import supported decorators directly from
`clauz3` or `deal`; ordinary import aliases such as `import clauz3 as c` and
`from clauz3 import trusted as boundary` are supported. Re-exporting a
decorator through a compatibility module, assigning
it to another variable (`boundary = clauz3.trusted`), or applying a custom
decorator is outside the supported declaration syntax. The prover refuses an
unrecognized decorator on a called function instead of treating the function
as undecorated and silently losing its effects or conditions. This also applies
inside `deal.chain(...)` and alongside an otherwise recognized boundary.
The empty `@deal.chain()` call is supported as an identity decorator; bare
`@deal.chain` and chains with keyword arguments are refused.
Use the direct import in the module that declares the function to repair the
declaration. An unsupported-decorator proof stops `clauz3 run` before approval
or program execution.

Permission to import a standard-library module does not imply support for its
decorators. Decorators such as `functools.cache` and `typing.overload` are not
modelled and are refused on called trusted-layer functions.

Before checking obligations, the prover checks the quantifier-free target
preconditions and called-helper postconditions for satisfiability. An
established contradiction produces a non-successful `vacuous` conclusion;
an undecided preliminary check is skipped rather than accepted as a proof.
A second bounded query includes quantified assumptions and generated Unicode
domains. It reports `vacuous` only when it establishes a contradiction; an
undecided second query leaves ordinary obligation checking unchanged.

No vacuity query is needed when the assumptions consist solely of generated
domains on unconstrained input symbols (and literal `True` facts). Any other
assumption retains the full check when quantified facts are present: even a
quantifier-free trusted postcondition can contradict a generated Unicode domain. A contradiction that the bounded
full query cannot establish remains a limitation of vacuity detection.

`@clauz3.trusted` marks a trusted side-effect boundary. It takes no
arguments: a function either is the boundary or it is not. A `@deal.has(...)`
carrying at least one marker is honoured as one too — but a bare
`@deal.has()` is not, because in deal it asserts that the function has *no*
side effects. When a called function is a trusted effect:

1. its preconditions are added as proof obligations
2. its body is not evaluated
3. the call is recorded as an effect fact
4. a fresh unconstrained symbolic value of the annotated return sort is
   materialized, narrowed only by the function's `@clauz3.ensures` contracts

The body that would have computed the return value is skipped, so the value is
invented from the return annotation. It is deliberately unconstrained: a
concrete stand-in would decide every branch that reads it at prove time, and an
effect guarded by the arm it ruled out would be proved unreachable while the
runtime still takes it.

Supported trusted return values are `bool`, `int`, `float`, `str`, the generic
containers `ann2type` understands, concrete `list[SomeRow]`, and generic
`list[T]` when a `clauz3.Row` schema argument is passed at the call site. Any
other return annotation, and a missing one, is refused with `UnsupportedError`
rather than guessed at, so a trusted function must annotate its return type.

A `-> None` return, and any call whose function has no `return` statement,
has a distinct `None` value with false truthiness. Conditions, `bool(value)`, and
`not value` are supported, so `if not effect(...)` stays live as it is at
runtime. Equality preserves its distinction from Boolean and integer values.
Arithmetic, ordering, container uses, and `and`/`or` operands are refused. A helper with explicit returns is also refused unless its control flow
is proved to return or raise on every path; dropping an implicit `None`
fallthrough would invent a return value. The control-flow check uses a solver
when simplification alone is insufficient, with a one-second timeout. A possible
fallthrough or an undecided result is refused.

## Effect Facts

A trusted call records:

```python
FactInfo(
    name="send_email",
    args={"addr": <symbolic str>, "msg": <symbolic str>},
    cond=<symbolic reachability condition>,
)
```

The fact's schema is inferred from the trusted function signature. A relation
names the functions it covers, and no name at all covers every trusted call:

```python
Email = effect(send_email)                 # the function, imported
Write = effect(write_file, append_file)    # several, sharing their fields
Any = effect()                             # every trusted call
```

A name that resolves to no trusted effect is refused rather than matching
nothing, and a predicate may only read fields the covered effects have. See
[Effect specs](effect-specs.md#effect-relations).

## Guarantees

`@clauz3.guarantee(...)` takes one positional `ContractSpec` built by a trusted
contracts module; stack one decorator per guarantee. It is a runtime no-op:
under plain Python it returns the function unchanged, and under `clauz3 run` it
is never evaluated at all. The prover reads the argument from the AST and
evaluates it as a symbolic guarantee after executing the target body; the
approval request and receipt bind to the same text. Removing a decorator
removes the contract; [coverage policies](coverage-policies.md) can report
the resulting gap when the trusted domain declares coverage requirements.

Example:

```python
@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    send_email("bob@example.com", "hi")
```

### Which decorators count

A decorator is recognised by the target it resolves to, not by how it is
spelled. All four of these state the same guarantee and are treated
identically by the prover, the approval request, and the interpreter:

```python
import clauz3                              # @clauz3.guarantee(...)
import clauz3 as c                         # @c.guarantee(...)
from clauz3 import guarantee               # @guarantee(...)
from clauz3 import guarantee as g          # @g(...)
```

Conversely, `import json as clauz3` makes `@clauz3.guarantee(...)` name
something that is not the clauz3 decorator, and it is refused.

`@clauz3.guarantee` is the **only** decorator an agent-authored entry file may
carry. Anything else — a trusted-only decorator such as `@clauz3.trusted`,
an
unmodelled library decorator such as `@functools.cache`, a locally defined
decorator, or a name no import binds — is a hard refusal from
`clauz3 prove` and `clauz3 run` alike. A decorator that cannot be resolved is
never ignored: silently dropping one is how a stated guarantee stopped being
load-bearing without the operator seeing anything change (issue #129).
### The guarantee grammar

A guarantee is the sentence the user consents to, so its text has to mean
something fixed. Unlike the rest of the program, the guarantee argument is not
an arbitrary expression from the subset above; it is restricted to:

```
guarantee := <registered @contract> "(" [ argument {"," argument} ] ")"
argument  := literal | row_class
literal   := str | bytes | int | float | bool | None | -number
           | list/tuple/set of literal
           | dict of literal -> literal
row_class := a name bound to a clauz3.Row subclass
```

"Registered" means decorated with `@contract` in a trusted root — the only
place `@contract` is allowed. The decorator itself must be called with exactly
one positional argument.

The registered helper runs as ordinary trusted Python. Its arguments retain
the Python values and types written in the guarantee, including `False`, zero,
negative zero, bytes, `None`, and nested literal containers. Containers are
constructed afresh for each call. Row class arguments retain their schema
identity. The bridge refuses computed or symbolic arguments instead of letting
a solver proxy's Python truthiness select a different contract. Relation
lambdas returned by the helper are still compiled symbolically and remain
subject to their supported expression subset.

Everything else is refused at prove time with a `ProverConfigError`, including
several shapes that used to report `proved!` over a program that violated the
claim, because `ProxySort.m_bool` treats a non-boolean value as true:

| Written | Why it is refused |
| --- | --- |
| `@clauz3.guarantee(emails.only)` | the helper is named, not called |
| `@clauz3.guarantee("only bob is emailed")` | prose is not a proposition |
| `@clauz3.guarantee(42)`, `@clauz3.guarantee(lambda: True)` | not a contract call |
| `@clauz3.guarantee(_predicate=emails.none())` | keyword form; used to be dropped in silence |
| `@clauz3.guarantee(only(["bob@example.com"]))` where `only` is defined in the entry file | not a registered `@contract` |
| `@clauz3.guarantee(emails.at_most(limit))` | the argument is not a literal the reader can see |

Below that gate, the solver independently refuses any guarantee expression that
does not evaluate to a boolean sort, so a shape no syntactic rule anticipated
skips the proof rather than discharging it.

## Effect-Spec Subset

Domain logic uses `clauz3.spec`:

```python
from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees


Email = effect("send_email")


@contract
def no_guarantees() -> ContractSpec:
    return core_no_guarantees()


@contract
def only(addresses: list[str]) -> ContractSpec:
    return Email.all(lambda e: e.addr in addresses)
```

The `@contract` function itself is executed as ordinary Python at prove time;
only the relation lambda inside it is parsed from source and compiled as a
small expression subset. That split is the trust boundary of the contract
layer. See
[Concepts → Trust boundary](../explanation/concepts.md#trust-boundary-what-runs-as-python-what-is-interpreted)
for what each side may do and why helper bodies must stay small.

### Relation Primitives

Currently supported:

- `no_guarantees()`
- `Relation.all(lambda row: predicate)`
- `Relation.where(lambda row: predicate)`
- `Relation.empty()`
- `Relation.distinct(lambda row: key)`
- `Relation.count() <= limit`
- `Relation.count() < limit`
- `Relation.count() >= limit`
- `Relation.count() > limit`
- `Relation.sum(lambda row: numeric_value) <= limit`
- `Relation.sum(lambda row: numeric_value) < limit`
- `Relation.sum(lambda row: numeric_value) >= limit`
- `Relation.sum(lambda row: numeric_value) > limit`
- `FilteredRelation.shares_value(other, lambda row: key)`

`where(...)` returns a filtered relation, so it composes with the other
relation primitives:

```python
Email.where(lambda e: e.addr == "bob@example.com").count() <= 2
```

This proves policies like "Bob is emailed at most twice."

`shares_value(...)` is an existential join between two filtered relation views:

```python
bob = Email.where(lambda e: e.addr == "bob@example.com")
ann = Email.where(lambda e: e.addr == "ann@example.com")
bob.shares_value(ann, lambda e: e.msg)
```

This proves policies like "Bob and Ann receive the same email content."

Reachability conditions are handled by the primitive. A contract author should
write:

```python
Email.all(lambda e: e.addr in addresses)
```

not:

```python
reachable(e) implies e.addr in addresses
```

### Relation Lambda Syntax

Relation lambdas support:

- constants: `bool`, `int`, `float`, `str`
- captured names whose values are supported literals or proxy values
- list literals
- row attributes: `e.addr`, `w.amount`
- comparisons: `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`
- boolean operators: `and`, `or`, `not`
- numeric `+` and `-`
- `len(...)` over supported row values such as strings
- the string methods `startswith` and `endswith`, as in
  `e.path.startswith(root)`

`and` and `or` use the scalar operand-selection rules described above, including
short-circuiting. A relation predicate that may raise an exception or creates an
unchecked proof obligation is refused; predicate evaluation cannot silently
discard those conditions after the entry function has returned.

Relation lambdas do not support:

- arbitrary function calls other than `len(...)`
- method calls other than the whitelisted `startswith` / `endswith`
- loops or comprehensions
- mutation
- dynamic attribute access, or attributes of anything but the row
- chained comparisons (unlike the program subset, which lowers them
  correctly; in relation lambdas, write `a < b and b < c`)
- bare row values such as `lambda e: e`
- a lambda nested inside another lambda on the same line (it cannot be
  located uniquely; wrap the factory in a `def` or put the inner lambda on
  its own line), lambdas whose source is not on disk, and two lambdas on one
  line when the interpreter was started with `-X no_debug_ranges` (a lambda
  alone on its line is still located by line number)
- `None` and `bytes` values, whether literal or captured
- tuple, set, and dict literal syntax (captured containers are supported)
- unary operators other than `not`, and binary operators other than `+` and `-`
- `is` / `is not`, conditional expressions, subscripts, slices, f-strings,
  nested lambda calls, and assignment expressions
- lambdas with more than one parameter, or a `def` passed where a lambda is
  expected

Unsupported lambda syntax raises `UnsupportedError`. The conformance cases in
`tests/test_relation_lambda_subset.py` cover accepted values and rejected syntax;
`tests/test_relation_lambdas.py` covers source lookup, including same-line lambdas.

## Fail-Closed Rule

Unsupported syntax, unsupported types, and unresolved names should not be
treated as safe. The intended contract is:

> If ClauZ3 cannot prove the requested guarantee over the supported subset, the
> program has not earned approval.

The current CLI exits non-zero when any proof obligation fails. Some unsupported
program constructs may appear as partial/skipped proof results; those should be
handled as rejection by any runner.

## Known Gaps

- Contract helper builder bodies are executed rather than fully AST-validated.
- Trusted functions with meaningful return values are not modeled.
- Chained comparisons in relation lambdas raise `UnsupportedError` rather
  than being lowered (the program-subset evaluator handles them correctly).
- Runtime receipt enforcement has not been implemented.
- Marker-level effect schemas are not validated up front.
- There is no formal grammar file for the subset yet; the relation-lambda
  subset has a conformance suite (`tests/test_relation_lambda_subset.py`)
  but the program subset does not.
- The language is not yet mapped to a Datalog, SQL, OWL, or SHACL frontend.

## Design Direction

The subset should move toward:

- a small named formal grammar
- tests for every accepted and rejected construct
- fully static validation of `@contract` helper bodies
- deterministic explanations for approval dialogs
- optional renderings into relational logic and description logic
