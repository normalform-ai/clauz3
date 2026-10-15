# Cross-layer resources

## Status

Historical design proposal for GitHub issue #58, written on 2026-09-04
against commit `d87247d`. The observations, experiment results, compatibility
claims, and work items below describe that snapshot; they are not current
runtime guarantees or supported API examples. The proposed `clauz3.resource`
API is not implemented.

Two prerequisites have changed since that snapshot:

- Effect relations now name trusted functions explicitly (for example,
  `effect(read_file, write_file)`); they no longer select calls by `@deal.has`
  marker strings. Unknown effect names are refused when the trusted layer is
  loaded. The marker-based experiments and additive migration proposal below
  must therefore be reevaluated before implementation.
- Trusted-layer isolation and root authorization landed in
  [PR #119](https://github.com/cmungall/agent-deal/pull/119), addressing the
  module-identity prerequisite discussed as #111 below.

The remaining design question is how explicit effect groups could project
differently named parameters onto shared resources. See the current
[effect specification reference](../reference/effect-specs.md) for the
supported API.

## The gap

A `@clauz3.guarantee(...)` is stated in one trusted layer's vocabulary —
`filesystem.only_write_under(D)` — but several layers act on the same
underlying resource. In the stdlib alone, `filesystem`, `editor`, `coreutils`,
`rsync`, `grep`, `find` and `git` all touch the filesystem. A guarantee about
the resource can therefore be bypassed through another layer's effect, or
become unprovable merely because another layer is present. Adding a tool must
not be able to open a hole in a policy that never mentioned it.

## Baseline behavior at `d87247d`

- A trusted call becomes a `FactInfo(name, markers, args, cond, quantifiers)`
  (`_context/_layer.py`). The trusted boundary in `_proxies/_func.py` records
  the fact and **skips the body**, so a trusted function that internally calls
  another trusted function (grep reading a file) is opaque: the outer
  function must declare the inner resource itself. Composition is by
  declaration, not by tracing.
- `effect(name).facts(ctx)` matches a fact when `name == fact.name` **or**
  `name in fact.markers` (`spec.py`). Markers are therefore already a
  cross-layer join key.
- The stdlib relies on that by convention: reads carry `read`, writes carry
  `write`, and the constrained parameter is called `path`. `editor`,
  `coreutils.make_dir`/`touch`, `rsync`, `grep` and `find` opt in by declaring
  the marker, and the grep/find contract modules re-export the filesystem
  contracts. Nothing checks that convention.
- `DomainPolicy.required` (`policy.py`) conjoins nullary contracts into the
  proof when a `when_used` name or marker is reachable. It is the only
  policy-scoping mechanism and lives per trusted root.
- Trusted modules resolve by dotted name through process-global import state
  (#111).

### Historical experiments

Five entry files against `clauz3 install stdlib:{filesystem,editor,coreutils,
rsync,env}` in one project, all five roots passed to `clauz3 prove`:

| Guarantee | Other layer's effect | Result |
| --- | --- | --- |
| `fs.only_write_under("/sandbox")` | `editor.edit_file("/etc/passwd", ...)` | `failed guarantee` — correct, the `write` marker joins |
| `fs.only_write_under("/sandbox")` | `editor.edit_file("/sandbox/notes.txt", ...)` | `proved!` |
| `fs.only_write_under("/sandbox")` | `coreutils.copy("/sandbox/payload", "/etc/cron.d/job")`; `coreutils.remove("/etc/passwd")` | **`proved!`** — silent bypass |
| `fs.only_write_under("/sandbox")` | `rsync("/sandbox/a", "/sandbox/b", False, [])` | `partially proved … unknown effect field path` |
| `fs.only_read_under("/sandbox")` | `env.read_env("HOME")` | `partially proved … unknown effect field path` |

## Failure modes

1. **Missing membership.** `coreutils.copy`/`move`/`remove` carry `copy`,
   `move`, `delete` but not `write`; `git` carries `git`, `io`, `syscall`.
   A filesystem guarantee is vacuously true over their facts and the proof
   passes. This fails **open**. Nothing can detect it, because membership is a
   string the layer author remembered (or did not) to add.
2. **Field-shape mismatch.** `rsync` does carry `write`, but its constrained
   parameter is `dest`, so `e.path` raises `UnsupportedError` and the proof
   is `SKIP`. This fails closed, but it makes the cross-cutting guarantee
   unusable in any program that also uses rsync — so rsync grows its own
   `only_dest_under`, and the policy fragments back into per-tool vocabulary.
3. **Marker collision.** `read` means "reads a file" (`filesystem`, `grep`,
   `find`), "reads rows" (`db.read_rows`) and "reads an environment variable"
   (`env.read_env`). The marker namespace is flat and untyped, so a
   resource-level guarantee cannot tell the resources apart; the env case
   above is `SKIP` for the same reason as rsync.
4. **Name collision (#111).** Two layers under `tools.db.trusted` resolve to
   whichever loaded first, process-wide. A guarantee proved against one
   layer's contract can be satisfied by the other layer's code, and the
   visible symptom was a security test passing on `SKIP`. This is the same
   defect one level down: the identity of a *layer*, like the identity of a
   *resource*, is a convention (a dotted name) rather than a checked
   declaration.
5. **Scoping.** The global constraint has nowhere to live. The decorator
   stack is per program; `required` is per trusted root and nullary, so it
   cannot carry the sandbox root `D`.

The common root: the join between a guarantee and the effects it governs is
three unchecked conventions — marker string, field name, module dotted name.
A mismatch fails open (1, 4) or closed-but-unusable (2, 3).

## Options

### A. Keep marker conventions, add a lint

Warn when a trusted function with a path-like parameter lacks `read`/`write`.
Cheap, but heuristic: it cannot know what a `subprocess` body touches, and it
does nothing for 3 or 5.

### B. Resource catalog with declared projections (recommended)

Declare each shared resource once, with its typed fields and the verbs
(families) that act on it:

```python
from clauz3.resource import resource

filesystem = resource("filesystem", fields={"path": str}, families=("read", "write", "delete"))
```

A trusted function binds to a resource by naming which of its parameters
supplies each field. Where it touches the resource more than once, it says so:

```python
@filesystem.read(path="src")
@filesystem.write(path="dest")
@deal.has("rsync", "trusted")
def rsync(src: str, dest: str, delete: bool, args: list[str]) -> str: ...
```

Semantically each binding is a **projection** of the function's fact onto the
resource: `filesystem.write(path=dest)`. The relation algebra is untouched —
`filesystem.write` is an `EffectRelation` whose `facts(ctx)` yields, for every
recorded fact of a bound function, a view with the arguments renamed per the
binding. That is the same shape as `Fluent._final_array`: resolved at
contract-solve time from `ctx.facts`, with no change to the vendored executor.
Contracts then range over the resource, not the tool:

```python
@contract
def confine_writes(root: str) -> ContractSpec:
    return filesystem.write.all(lambda w: w.path.startswith(root))

@contract
def no_filesystem_access() -> ContractSpec:
    return filesystem.any.empty()
```

- *Soundness.* A binding is authored in the trusted root under the same trust
  as `@deal.has`. Failure mode 2 disappears: the field map is explicit and
  validated at import time (the named parameter must exist). Failure mode 3
  disappears: families are attributes of a resource object, not strings.
  Failure mode 1 becomes detectable: an effect bound to no resource is
  reportable by `clauz3 tools`, and a project can make it a
  `ProverConfigError` — the fail-loud counterpart of
  `_validate_required_triggers`. Failure mode 6 in the issue (universal
  absence) is `filesystem.any.empty()`, sound exactly when membership is
  complete, which is why the unbound-effect check matters.
- *Authoring burden.* One decorator line per resource-touching effect; the
  stdlib filesystem family is about twenty functions. The explicit field
  map is the point, not overhead.
- *Compatibility.* Additive. `effect("write")` marker relations keep working;
  existing cases and contracts are unchanged. Layers migrate one at a time.

### C. Policy-layer composition only

Extend `DomainPolicy.required` to carry arguments and allow a project-level
`policy.py`. This answers question 4 in the issue (where the policy lives) but
still keys on marker strings, so 1–3 remain. Worth doing *after* B, as the
place the global constraint is stated:

```python
POLICY = domain_policy(
    when_used=filesystem,                     # any effect bound to the resource
    required=[confine_writes("/sandbox")],     # argument-carrying, project-level
)
```

### D. Trace through trusted bodies

Symbolically execute trusted bodies so grep's inner `read_file` is observed.
Rejected: bodies are arbitrary Python (`subprocess`, DuckDB), and the boundary
skip is what makes trusted layers cheap to write and audit.

## Answers to the issue's questions

- *Resource-level effects vs. effect tagging (1, 2).* Both, as one mechanism:
  the resource is the type, the binding is the tag, and the field map is what
  makes the tag joinable.
- *Transitive composition (3).* Stays declarative. A wrapping layer binds the
  inner resource itself; the unbound-effect check catches the forgotten case.
- *Policy scoping (4).* Option C on top of B.
- *Namespace / equivalence (5).* `read_file` and `grep` are different
  *effects* with their own facts and contracts that project onto the same
  resource *family*. The equivalence is the declared binding, not a name.
  Layer identity itself needs #111's path-keyed resolution first.
- *Negative composability (6).* `filesystem.any.empty()`, conditional on
  complete membership.

## Original proposed work items

1. Land #111 (path-keyed trusted-module resolution or per-root isolation, and
   strict `Conclusion.FAIL` assertions in the suite). A catalog is only as
   trustworthy as knowing which layer was loaded.
2. Add `clauz3.resource`: `resource()`, family decorators recording
   `func name -> [(family, field map)]`, an import-time check that mapped
   parameters exist, and a `facts(ctx)` that projects `ctx.facts` — the
   `fluent.effect` pattern, `_vendor` untouched.
3. Migrate the stdlib filesystem family (`filesystem`, `editor`, `coreutils`,
   `rsync`, `grep`, `find`; `git` binds `cwd` as a `write`) and add
   `confine_writes` / `no_filesystem_access`. Turn the coreutils experiment
   above into a `_fail` case that now fails.
4. `clauz3 tools` lists unbound effects; an opt-in flag makes them a
   `ProverConfigError`.
5. Project-level `policy.py` with argument-carrying `required` (Option C).

## Relationship to other todos

- [effect-ir.md](effect-ir.md) — projected facts are additional effect atoms;
  the IR grammar should carry `resource.family` atoms so the DL/NL renderings
  can speak in resources rather than tool names.
- [approval-policies.md](approval-policies.md) and the
  [coverage policies reference](../reference/coverage-policies.md) — `required`
  is the enforcement hook Option C extends.
- [cedar.md](../explanation/cedar.md) — Cedar's typed resource entities and
  schema validation are the closest analogue to the catalog.
