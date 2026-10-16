# Own the interpreter instead of fencing off CPython

Status: **landed.** `clauz3.interp` is the runtime for `clauz3 run`. The
`exec` path and its containment machinery are deleted.

This note is kept as the rationale and as the record of what the change cost —
including several rounds of review findings against the implementation, which
are the most useful part of it.

## The problem

`clauz3 run` proves a program and then hands it to CPython:

```python
exec(compile(module, source_name, "exec"), namespace)
```

Everything between the proof and that `exec` exists to stop the *rest of
Python* from being reachable once the proved program is running:

| Mechanism in `runner.py` | Lines | What it is defending against |
| --- | --- | --- |
| `FORBIDDEN_BUILTINS` | 35 | `eval`, `exec`, `type`, `help`, `open`, … |
| `FORBIDDEN_ATTRIBUTES` | 24 | `__class__`, `mro`, `gi_frame`, `format`, … |
| `_validate_runtime_source` | 29 | walking the AST looking for the above |
| `_validate_module_level` + 4 helpers | 94 | code that runs at `exec` time |
| `_sanitize_for_exec` + `_is_guarantee_decorator` | 34 | decorators and annotations |
| `_safe_builtins` + `_make_guarded_import` | 26 | the namespace handed to `exec` |
| `_execute_source` | 32 | `sys.path` / `os.environ` juggling |
| **Total** | **283** | |

`tests/test_runtime_sandbox_hardening.py` is candid about how well this works.
Its own docstring says:

> These are harm-reduction stopgaps, not a security boundary — the only real fix
> is OS-level isolation.

The trajectory is visible in the tree. Issue #68 added `type`/`super`/`object`
to the builtin denylist, `mro`/`gi_frame`/`f_globals`/`format` to the attribute
denylist, and removed `operator` from the import allowlist — because
`operator.attrgetter("__globals__")` takes a *string*, so it slips past an AST
denylist that matches attribute syntax. PR #106 adds another 1220 lines to
align what `prove` and `run` each consider acceptable source. Every one of
these is correct, and every one of them was found by someone thinking of a new
path from a live Python object back to `os`.

The structural issue: **the proof is over a small subset, but the executor is
CPython**. The validators exist to shrink CPython back down to the subset from
the outside, and that direction of travel has no natural end.

## The inversion

Run the proved subset with our own interpreter. `clauz3.interp` walks the same
astroid tree the prover walks, with a concrete value domain instead of a
symbolic one.

The prover is *already* an interpreter — an abstract one, over Z3 sorts.
`deal_solver._eval_stmt` and `_eval_expr` are dispatch tables keyed on astroid
node type, and an unregistered node raises `UnsupportedError`. The interpreter
is the same two tables with a different value domain:

```
                  astroid.parse(source)
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
   deal_solver._eval_expr      clauz3.interp.eval_expr
   (values = Z3 sorts)         (values = None/bool/int/float/str/
              │                 containers/Row)
              ▼                         ▼
          Proof                  Effects, in order
              │                         │
              └──── same node set ──────┘
```

Nothing shrinks CPython down to the subset, because CPython never receives the
program. A construct outside the subset has no handler, so it cannot run.

### What actually goes away

Not mitigated — *absent*:

- **`FORBIDDEN_BUILTINS`.** `clauz3.interp.builtins` is an allowlist of 15
  implementations. `eval` is not a name it refuses; it is a name that does not
  exist. `type(()).mro()` fails at `type`, before `mro` is ever reached.
- **`FORBIDDEN_ATTRIBUTES`.** The subset has no *general* attribute access: the
  prover's proxies expose a fixed `methods` table and `Row` exposes its declared
  fields, and `clauz3/interp/methods.py` mirrors exactly that surface.
  `x.__class__`, `g.gi_frame.f_globals` and `"{0.__class__}".format(())` are
  not denied — there is no rule that
  would resolve them. `tests/test_interp_escapes.py` asserts both halves: every
  payload fails, *and* no entry in `FORBIDDEN_ATTRIBUTES` appears as a special
  case anywhere in `clauz3/interp/`.
- **The guarded `__import__`.** Import resolution is a method on `Program`.
  There is no `__import__` in the value domain, so there is no hook to bypass —
  the `operator.attrgetter` class of bypass has nothing to attach to.
- **`_sanitize_for_exec`.** Decorators are matched by name and never evaluated;
  annotations are never evaluated. `@evil()` and `x: send_email(...) = 5` are
  not rewritten away before `exec` — nothing evaluates them in the first place.
- **`_check_class_def`'s metaclass/base/keyword rules.** A `Row` schema is read
  out of the class body as syntax. There is no namespace to populate and no
  metaclass hook to fire.

### What does *not* go away, and the mistake of thinking it did

`_validate_module_level` the *function* is deleted, but the **rule** it enforced
survives — moved into `_exec_module` / `_require_inert`. It changes character:
it is a *soundness* rule (the proof covers the target's body, so anything else
that runs is unproved) rather than a *containment* rule. The constant-expression
checks on module-level assignment values, assignment targets and default
arguments all stay.

This draft originally got that wrong, and it is worth recording why. The first
version listed "the non-constant-default rule" here under *what goes away*, on
the reasoning that `runner.py` only needs it because *CPython* evaluates the
default at `exec` time, whereas the interpreter evaluates it itself. That
reasoning applies the containment/soundness distinction and then lands on the
wrong side of it. It does not matter **who** evaluates an effect outside the
proved body; it matters that it is outside. Both of these ran a real trusted
effect before `main()` was ever entered:

```python
SENT = send_email('eve@example.com', 'exfil')          # module-level Assign

def helper(x=send_email('eve@example.com', 'exfil')):  # module-level default
    ...
```

Perfectly contained, entirely unproved -- and `Outcome.effects`, offered above
as "directly comparable against what the proof reasoned about", contained calls
the proof never saw. Both are now gated, and regression tests parametrise over
the `Assign`, `AnnAssign`, default, and helper-call forms and assert no effect
fires.

The default half of that fix was **still wrong**, and in the same way, for four
more review rounds. It was gated on `frame.module_scope`, exempting a nested
`def` on the reasoning that such a `def` is reached only along a proved path.
Reachability along a proved path is not the same as the expression having been
proved. `deal_solver.eval_func_def` binds a `FuncSort` for the node and never
walks `node.args`, so a default is unevaluated by the prover *wherever* it
appears:

```python
@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    def helper(x=send_email("eve@example.com", "exfil")):
        return x
```

`PROOF : [('main', 'proved!')]` — the guarantee quantifies over an empty effect
set — and then `RUN : effects = ['...send_email']`. A false proof on the live
path, and a test named `test_nested_defaults_may_be_expressions` was pinning it
as correct. The gate is now `_require_literal`, scope-independent because the
prover's blind spot is, and it covers `arguments.kw_defaults` as well as
`arguments.defaults` (`runner._check_defaults` had walked both; the first
version of this one walked only the first list).

The *next* review round found the same class of mistake in the fix itself. The
gate was applied to assignment **values** but not assignment **targets**, and
`_assign_to` evaluates a subscript target's expressions:

```python
CFG = {}                                       # constant value, passes the gate
CFG[send_email('eve@example.com', 'exfil')] = 1  # ... target fires the effect
```

`runner._validate_module_level` had the identical blind spot on the `exec`
path — it checked `stmt.value` and never looked at `stmt.targets` — so this was
a live soundness hole in the shipped runtime, not only in the prototype.

And then the round after *that* found the same shape a third time, one scope
further down: `runner._is_declarative_member` validates a class body's
assignment values without looking at their targets, and `exec` runs a class
body. `class Schema: CFG[send_email(...)] = 1` fired the effect at
class-definition time, before `main()`, and outside the effect set the approval
request was built from — so the operator signs a receipt that omits it. All
three are fixed.

A fourth position, found the same round as the nested default: `assert cond,
MSG` and `raise E(ARG)`. `eval_assert` evaluates `node.test` and nothing else;
`eval_raise` collects exception *names* and evaluates no operand at all. Both
handlers here ran the operand through `eval_expr`, so an effect in a failure
message or an exception argument fired — on exactly the paths where the program
was already going wrong. Both now render the operand with `as_string()`: the
diagnostic keeps the source text and performs no work.

And a fifth, the round after: the `lambda` spelling of a default.
`_expr_lambda` never looked at `node.args`, and `call()` bound lambdas with an
empty `defaults`, so
`lambda x=send_email(...): x` was neither evaluated nor refused — the effect
was **silently discarded**, which is worse than either. The catalogue had the
`def` spelling at module level *and* nested in a body, and still missed this,
because the catalogue enumerates positions rather than deriving them. (The same
omission had a plain bug in it: `lambda x=1: x` then `f()` raised "missing
argument", for a literal the gate would have admitted.)

The pre-flight added in point 5 promptly produced a sixth, in the mirror
direction. It is a *second* statement of the subset, so it can disagree with
the first — and it did: `astroid.Starred` was in neither registry nor the
scaffolding exemption, so `A, *B = (1, 2, 3)` ran under `interp.execute` and
was refused by `clauz3 run`. The test asserting the form is allowed stayed
green, because its helper reimplemented the pre-flight instead of calling it,
while claiming in its own docstring to be "everything the runtime does before
the target is called". The same tuple was too *loose* in the other direction:
`AssignAttr`, `DelName` and `ExceptHandler` were exempted although no handler
reads them, so `obj.attr = 1` passed the pre-flight and was refused only after
the nonce was burned. Every entry now names its reader in a comment, and the
helper calls the shipped function.

The general lesson for the migration, now with six data points in a row:
containment is the part this design makes *structural*, and it is tempting to
assume soundness comes along for free. It does not. Every rule in `runner.py`
needs classifying individually, the soundness ones must be carried over rather
than reasoned away — and each one needs testing at **every syntactic position
the evaluator can be entered from**, not just the obvious one. Value but not
target; module body but not class body; the assigning body but not a nested one.
A regression test pinning only the obvious form stays green over the hole, and
it has done so four rounds running — including once for a rule whose own
comment called this out as the failure mode it could not tolerate.

That is an argument for the design rather than against it, but only if the
implication is taken seriously: the soundness gates are the part that stays
hand-written, so they are where the review effort belongs once containment
stops needing any.

## Evidence

`tests/test_interp_corpus.py` runs the entire shipped corpus — 254 case files
across `src/clauz3/stdlib/` and `examples/` — through the interpreter:

- **246 interpreted.** Trusted effects are stubbed through `trusted_dispatch`,
  so the sweep does no real I/O.
- **8 not run**, all of them prove-only fixtures whose target takes symbolic
  parameters (`def main(use_savings: bool)`). `clauz3 run` cannot run these
  either — it calls `main()` with no arguments.

So the subset is not a toy: it covers 100% of the runnable corpus. The AST
vocabulary those 254 files actually use is 18 node types.

`tests/test_interp_parity.py` pins the two dispatch tables together. The
**expression registries are identical** — 16 node types, no difference in
either direction. The statement registries differ by three nodes, each with a
recorded reason (`ClassDef`/`AnnAssign` interpreter-only, `Global`
prover-only). `AugAssign` was once interpreter-only -- runnable but unprovable
-- and was removed until the prover gained a matching handler (#109); both now
desugar it for the same four immutable sorts. A future divergence in either
direction fails that test:
a node the prover learns but the interpreter cannot run is a gap; a node the
interpreter would run but the prover cannot reason about is a hole.

### A hole writing it found

Writing the interpreter surfaced one immediately. `imports_policy.py` admits 13
pure stdlib modules for untrusted programs. The prover models functions from
**three** of them (`math`, `re`, `os.path`), and only a handful from each:
`math.sin`, `math.isclose`, `math.isinf`, `math.isnan`, `math.trunc`.

Under `clauz3 run`, `math.sqrt(4)` and `json.loads(blob)` execute happily. The
prover has no model for either, so the theorem is `SKIP` and the run is
correctly refused — but that gate is a *proof conclusion*, not a shared subset
definition. The interpreter joins the two tables directly: a library module
exposes exactly the names in `deal_solver._funcs.FUNCTIONS`
(`exposed_names` in `interpreter.py`). `math.sin` runs; `math.sqrt` does not.

This is the difference the whole design turns on. "The prover happened to
choke" is not the same guarantee as "the executor cannot run it".

The same gap reappeared one table over, which is instructive. `builtins.py`
states the rule in its docstring -- "a builtin the prover cannot model must not
be callable at runtime" -- and then shipped `sorted`, which the prover does not
model at all. Prose stating a rule is not a test enforcing it;
`test_builtin_tables_agree` now compares `BUILTINS` against `FUNCTIONS`
directly, with `range` as the one recorded exemption (the prover handles it
structurally in `for` headers rather than as a call).

Several further parity gaps came out of review rather than the corpus. Two of
the "runs but cannot be proved" shape: recursion through a `lambda` escaped the
recursion refusal, because every lambda is named `<lambda>` and the trace was
keyed on names (now keyed on node identity); and `import a.b.c` without an alias
needs package traversal the subset does not have, so it is refused by name
rather than failing with a message about a module the author never wrote.

Two in the mirror direction — *provable but not runnable*. The larger one:
the prover resolves attribute access through each proxy sort's `methods` table,
and 42 of those entries have real implementations rather than the `unsupported`
stub — so `'sandbox/x'.startswith('sandbox')` **proves today**, and the
interpreter refused all attribute access on data. That made the interpreter
narrower than the proved subset, and migration step 5 would have silently
stopped a class of already-proved programs running. `clauz3/interp/methods.py`
now implements exactly that surface, joined against the prover's registry by
`test_value_method_surface_matches_the_prover`. Note what hid it: the
expression registries are identical because `Attribute` is registered on both
sides — node-type parity says nothing about what each side does *inside* a
handler.

That lesson then had to be applied twice more, in the statement table.
`eval_assign` accepts only `AssignName` and `Subscript` targets; `eval_for`
requires an `AssignName` target and walks the body refusing `Break`/`Continue`/
`Return`; and the prover has no `ClassDef` handler at all. So `a, b = 1, 2`,
`for a, b in [(1, 2)]`, `for n in [...]: return n` and a `class` inside a proved
body all ran here and skipped there — while `test_statement_subsets_agree`
passed, because `Assign` and `For` are registered on both sides. **A shared node
type is not a shared subset.** Each is now refused, with the module-scope
exemption where the prover never looks.

And a third place the same lesson applies: the per-operator dispatch tables
inside `BinOp`, `Compare` and `UnaryOp`. Node-type parity is silent about them,
and `_COMPARISONS` shipped `is` / `not in`-style entries for `is` and `is not`,
which `deal_solver.COMAPARISON` has no entry for — runnable here, `UnsupportedError`
there. They are removed, and `test_operator_tables_are_a_subset_of_the_prover`
now asserts the one-directional rule (the interpreter may refuse an operator the
prover models; never the reverse) with `@` enumerated as the single deliberate
omission.

The smaller one: `re.compile`,
`re.match` and `re.fullmatch` are modelled by the prover, and `re` is in the
import allowlist, but all three return a live `Pattern`/`Match` that
`marshal_in` can never admit. They used to run and then die inside the
boundary; they are now refused at the name, listed in
`LIBRARY_RETURNS_OUTSIDE_DOMAIN`, and covered by
`test_library_surface_matches_the_prover` — which exists because
`test_builtin_tables_agree` filtered `FUNCTIONS` to `builtins.*` and left the
library half of the join unguarded.

Note also what the corpus sweep does *not* show. `_stub` synthesises `[]` for
any list-returning trusted function, so a `for row in read_rows(...)` body never
executes. 246/254 is honest about *entry* coverage and overstates *path*
coverage; a differential harness against real trusted layers is what would close
that.

### Issue #97 falls out

Two `def main`s: astroid infers one, Python executes the last. Because the
interpreter resolves names by executing the module top to bottom, the node it
*will* run is knowable before it runs — `interp.target_node()` returns it. Hand
that node to the prover and both sides are pinned to the same object, rather
than to two independent resolutions that a validator has to keep in agreement.

With one caveat learned the hard way: `target_node` must **share** the
resolution, not reimplement it. The first version scanned `module.body` for the
last `FunctionDef` *named* `main`, which is a different question from what
`main` is bound to — `from x import y as main` and `class main(Row)` both make
the two disagree, and wiring a disagreeing answer to the prover would prove one
body while running another. It now calls the same `_load_module` /
`_resolve_target` pair `execute` does. Re-deriving an answer that must match is
how these two ended up out of step in the first place.

**Nothing calls `target_node` yet.** Closing #97 means changing how the prover
selects the theorem, which is a different change from replacing the runtime, and
doing it here would mean editing the prover in a PR about the executor. What
landed is the executor half — the half that is easy to get subtly wrong — with
tests pinning the rebinding cases and the module-level effect gate.

## The trusted boundary is where the risk moves

The hand-off to CPython stays, and it stays deliberate. Trusted layers are
full-expressivity Python — they open sockets, write files, shell out — and the
`@deal.has` markers on them are what the proof reasons about. The interpreter
calls them for real.

Under `exec`, the boundary is not a boundary: the untrusted program and the
trusted layer share one object space, and `send_email` returning a live object
adds nothing, because the program already holds `str`s and `list`s with
`__class__` on them. That is why `runner.py` has no marshalling code at all —
it would be pointless. The denylist is trying to cut *every* path at once.

Under the interpreter, the program's object space is deliberately barren. There
is no general attribute access and no user-defined class, so the only route from
an interpreted program to a live Python object is through the trusted boundary.
Cutting one path is a far better position than cutting all of them — but it
means **the boundary now carries weight it did not carry before**. Getting it
wrong is not a partial regression; it restores the whole of CPython.

Three checks hold it:

- **Out** (`marshal_out`): only subset data. A `UserFunction` cannot be handed
  to a trusted layer that would call it back outside the interpreter.
- **In** (`marshal_in`): only subset data, copied. A trusted function returning
  a live object is a `TrustedBoundaryError`, not a value.
- **Surface** (`exposed_names`): what an imported module makes nameable.

### The first implementation got the third one wrong

Worth recording, because it is the whole argument in miniature. The first
version wrapped any module attribute for interpreted use. Python module
attributes are transitive — `import os` at the top of `effects.py` makes
`effects.os` an attribute of the module — so this was a live escape through a
trusted layer that had done nothing wrong:

```python
from tools.demo.trusted import effects

def main() -> None:
    effects.os.system("id > /tmp/pwned")     # executed; wrote the file
```

`exposed_names` now computes each module's surface once. A trusted layer exposes
what it **defines** — functions and `Row` schemas whose `__module__` is that
module — plus subset constants. Never what it imports; never a module. Regression
test: `test_interp_escapes.py::test_trusted_layer_imports_are_not_its_surface`.

Two things are worth noticing about how that bug behaved.

It was **not** a containment collapse on its own. The value domain is re-checked
at every consumption point — `_expr_attribute`, `call`, `_require_operand`,
`_truth`, `_require_data` — so a leaked object that merely *sat* in a variable
would have been inert. What made it exploitable was specifically that
`wrap_trusted` turned a module into a `TrustedModule`, which is a callable
gateway by construction. The lesson is narrow and useful: **leaking data is
survivable, leaking a gateway is not.**

And it was found by writing the interpreter, in a place where the fix is nine
lines and a test. Under `exec` the equivalent hole is not a hole at all, because
`os` was reachable anyway — which is the point. This design converts a diffuse,
unbounded risk into a small number of concrete chokepoints that can be named,
tested, and reviewed. That is the trade: fewer places to get it wrong, and much
worse consequences for getting one of them wrong.

Every trusted call also goes through one place, which has a second payoff:
dry-run, audit log, and record/replay are a `trusted_dispatch` parameter rather
than a monkeypatch. `Outcome.effects` is the observed effect sequence, directly
comparable against what the proof reasoned about.

Extending the value domain is a visible edit. The prototype needed one:
`read_rows(UserRow, ...)` passes a `Row` *schema* across, so `RowClass` is
admitted in both directions — a deliberate line in `values.py`, not a silent
pass-through.

### What this asks of trusted-layer authors

Under `exec` a trusted layer author has no security obligations to the untrusted
program, because there is no boundary to uphold. Under the interpreter there are
two, and they should be documented alongside the #91 runtime-validation
obligation:

1. **Return subset data.** Enforced — `marshal_in` refuses anything else — but
   it turns a design slip into a runtime failure, so it belongs in the contract.
2. **Do not hand out gateways.** A trusted function that returns a callable, or
   a `Row` subclass with a live object hiding in a field, widens the surface.
   `marshal_in` catches the current shapes; the general rule is that a trusted
   return value should be inspectable data, not machinery.
3. **Preserve `__module__` on any decorator.** A layer's interpreted surface is
   what it *defines*, computed from `__module__` and widened to the trusted
   prefix so a layer may split across files and re-export. `@deal.has` and
   `@deal.pre` survive that only because they use `functools.wraps`; a
   trusted-layer decorator that dropped `__module__` would silently empty the
   layer's surface.

## What this does not fix

Being straight about the limits:

1. **Trusted layers are still arbitrary Python.** That is the design, not a
   leak — but see "the trusted boundary is where the risk moves" above: the
   marshalling and surface checks are now load-bearing in a way nothing in
   `runner.py` is, and one bug shipped there during development. Issue #91
   (guarantees over external identifiers can be vacuously satisfied) is
   unaffected.
2. **`marshal_out` copies mutable containers.** A trusted function that appends
   to a list argument mutates the caller's object under `exec` and has no
   observable effect here. The copy is deliberate — a trusted layer must not
   retain a live handle on interpreter state — so this is a real semantic
   difference, documented rather than removed. `Row` instances are *rebuilt*
   from marshalled fields in both directions (`_marshal_row`), so the invariant
   holds through a Row and not merely beside one; the residual is a
   non-declared attribute set via `object.__setattr__`, which no interpreted
   program can read back because `_expr_attribute` resolves declared fields
   only.
3. **Trusted-module selection has a separate lifecycle.** Issue #111 / PR #119
   adds shared selection of verified Python modules, Astroid sources, and
   registrations. The interpreter retains that selection through actual entry
   execution and lazy trusted imports; displaced host bindings are restored
   afterward. Trusted code remains ordinary Python.
4. **Prover-side soundness bugs are unaffected.** Issues #110 (negative `int()`),
   #103. (#43, chained comparisons, is *fixed*: `eval_compare` conjoins every
   pair and reuses the previously-evaluated right as the next left, so
   `test_chained_comparison_uses_full_python_semantics` is a parity test, not
   a divergence one — an earlier draft of this note said otherwise and was
   describing the pre-fix prover.) The interpreter makes the remaining ones
   *testable*, but it does not fix them. It does turn "the prover disagrees with CPython" into
   "the prover disagrees with our own interpreter, and here is the differential
   test", which is a much easier thing to close.
5. **Semantic drift is now our problem.** Today the runtime is CPython and is
   therefore right by definition. With an interpreter, every operator is a
   choice we own. Mitigation: subset operators delegate to CPython's on subset
   data — there are no user-defined classes, so `a + b` cannot dispatch into
   attacker-written `__add__` — and the divergences that remain are the prover's,
   which we wanted to find anyway.
6. **A dotted `--allow-import` opt-in is a no-op here.** It imports, then
   exposes nothing: outside every trusted root it is treated as a library, and
   the prover models none of its functions. That is the correct default — the
   alternative is an import flag silently granting trust — but it makes a scoped
   operator opt-in useless under the interpreter rather than merely narrow. The
   diagnostic now says so instead of printing an empty allowlist.
7. **Not OS-level isolation.** A CPython bug reachable through `str`/`int`
   operations, or a resource exhaustion in a trusted layer, is out of scope.
   The step budget bounds evaluated *nodes*, not work: `2 ** (10 ** 9)` is a
   handful of nodes and unbounded time, and a trusted call that hangs is
   untouched. Bounding work needs operand-magnitude limits this does
   not have. This is a much smaller residual than the current denylist, but it
   is not zero, and the honest claim is "no in-process escape path through the
   language surface", not "sandboxed".

## Cost

`clauz3/interp/` is 941 logical SLOC — 2,355 lines counting docstrings and
comments, which is the honest figure to weigh against the 295 lines deleted from
`runner.py` (which is now 150 SLOC, down from 445) — plus whatever the denylist would have
accreted next, which is the real saving. the interpreter test suite, all passing;
`just check` clean.

It took roughly as long to write as reading PR #106's diff, which is the
strongest argument for the approach: writing an interpreter for *general*
Python is a massive task, but this subset is 16 expression forms and 14
statement forms, and we already have one interpreter for it.

## What landed

The staged path this note originally proposed — ship behind `--interpret`, run
both, flip later — was the wrong shape. Running two runtimes side by side keeps
both, and keeping the denylist alongside the interpreter preserves exactly the
maintenance burden the change exists to remove. So:

1. `runner._execute_source` calls `clauz3.interp.execute`. There is no `exec`.
2. **Deleted** from `runner.py`: `FORBIDDEN_BUILTINS`, `FORBIDDEN_ATTRIBUTES`,
   the denylist walk in `_validate_runtime_source`, `_validate_module_level` and
   its five helpers, `_sanitize_for_exec`, `_is_guarantee_decorator`,
   `_safe_builtins`, `_make_guarded_import`, `_temporary_sys_path` — 295 lines.
   `runner.py` keeps proof, approval, receipt verification, and two pre-flight
   checks that are genuinely prior to execution: the trusted-decorator gate (a
   *prove-time* rule about who may declare an effect) and the import allowlist
   (shared with `prove`, so an agent cannot slip past one into the other).
3. The soundness rules that used to live in `_validate_module_level` are
   enforced by `_exec_module` / `_require_inert` / `_require_inert_target`,
   against the same tree the prover walked — one copy instead of two that had to
   agree.
4. `RunOutcome.effects` now carries the trusted calls the program actually made,
   in order. The `exec` path could not observe that at all.
5. Structural refusal runs **before** the approval round trip
   (`runner._preflight_structure`). Moving the rules into `interp.execute` had
   moved them past `prove_text`, past the approval request, past the human
   decision, and past the nonce consumption in `_verify_receipt_or_refuse` —
   so `LIMIT = OTHER`, which proves OK because the prover never evaluates
   module level, burned a receipt on a run that never happened. The deleted
   `_validate_module_level` ran before proving. The pre-flight re-establishes
   that for the two classes decidable without running: module level and target
   resolution (via `target_node`, under the same inertness gates) and subset
   vocabulary (a static walk against the two registries).
6. `clauz3 run --step-budget N` exists. Exhaustion aborts a proved, approved
   program *mid-run*, so the message now names the effects that already fired —
   without that, nothing distinguished a partial run from a refused one.
7. A program *fault* is not a *configuration* fault. `ProgramError` and
   `ExecutionLimitExceeded` share a `ProgramFault` base and become
   `RunProgramError` — `clauz3 run` exit 1, the code the `exec` path produced
   for an uncaught exception. Exit 2 stays reserved for "this source should not
   have been run". The first version of the swap wrapped every `InterpError` as
   `RunConfigError`, which told an agent to rewrite a program that had been
   accepted and had simply failed.

`tests/test_runtime_sandbox_hardening.py` is now a pointer: its payloads are
asserted against the real runtime in `tests/test_interp_escapes.py`, which keeps
the two denylists as a *catalogue* and checks something stronger — not that each
name is refused, but that none of them resolves to anything, and that none is
named anywhere in `clauz3/interp/`.

The differential harness is now implemented. It samples the assumptions of
successful corpus proofs, runs the interpreter with generated trusted returns,
and checks observed effects and contracts. See
[Differential testing](../explanation/differential-testing.md) for the command,
coverage accounting, and remaining bounded-sampling limitations.

The harness exposed a false proof from using `False` as the symbolic value
of a void call. With a trusted `emit(value: int) -> None` and a guarantee that
every emitted value is at most 1, the prover incorrectly accepted:

```python
value = emit(0)
if value != False:
    emit(2)
```

The prover treated the branch as unreachable, but Python's `None != False`
takes it. Void call results now retain false truthiness without being Boolean
values: unsupported value operations are refused. This applies to trusted
void calls and helpers with no return value. The regression now requires a
non-OK proof, with no expected-failure exemption.

### Still open

- **Wiring `target_node` into the prover** to close #97, per the caveat above.
- **The step budget is not a termination guarantee.** It bounds evaluated
  nodes, not work: `2 ** (10 ** 9)` is a handful of nodes and unbounded time.
  Closing that needs operand-magnitude limits on `**`/`*`/`<<`.
- **`re` proves and cannot run.** `re.compile`/`match`/`fullmatch` are modelled
  by `deal_solver`, `re` is in the import allowlist, and all three return live
  `Pattern`/`Match` objects that `marshal_in` can never admit — so a program
  branching on `re.match(...)` proves OK and is then refused. The refusal now
  says so plainly rather than claiming the prover models nothing, and the
  pre-flight cannot hoist it: `re.match` is an `Attribute` node *with* a
  handler, and only resolving it against the module's surface refuses it.
  Deciding that statically would mean a second interpreter — the thing this
  design exists to avoid. The real fix is a prove-time skip on
  `LIBRARY_RETURNS_OUTSIDE_DOMAIN`, which is a prover change.
- **Issue #106** rewrites the same `runner.py` validator this change deletes
  most of; the two need reconciling. Its soundness half (duplicate-target
  selection) survives and is still worth landing.
- **Issue #111 / PR #119** coordinates colliding trusted modules across proof,
  policy evaluation, approval waits, and execution. See the reference's import
  lifecycle for identity, freshness, and concurrency limits.
