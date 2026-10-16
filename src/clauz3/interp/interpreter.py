"""A tree-walking interpreter for the clauz3-proved Python subset.

Motivation
----------

``clauz3.runner`` proves a program and then hands it to CPython via ``exec``.
Everything between those two steps -- the forbidden-builtin list, the forbidden-
attribute list, the guarded ``__import__``, the module-level validator, the
AST rewrite before ``exec`` -- exists to stop the *rest of Python* from being
reachable once the proved program is running. That is an open-ended denylist
against a language that keeps offering new paths back to ``os``.

This module inverts it. The program is never handed to CPython at all: the same
astroid tree the prover walks symbolically is walked here concretely. A
construct outside the subset has no handler and therefore cannot execute, so
"what was proved" and "what runs" are two readings of one tree rather than two
different languages that a validator has to keep in alignment.

CPython is still reached, but only through :class:`~clauz3.interp.values.
TrustedCallable` -- the trusted-layer functions carrying ``@clauz3.trusted``,
which are full-expressivity Python on purpose. The values crossing that call are
marshalled in both directions.

Status: prototype. See ``docs/todos/subset-interpreter.md``.
"""

from __future__ import annotations

import builtins
import functools
import math
import ntpath
import posixpath
import sys
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import astroid

import clauz3
from clauz3 import decorators as decorators_mod
from clauz3.imports_policy import ImportPolicy
from clauz3.row import Row

from . import methods as subset_methods
from .builtins import BUILTINS
from .errors import (
    ExecutionLimitExceeded,
    InterpError,
    NameResolutionError,
    ProgramError,
    TrustedBoundaryError,
    UnsupportedNode,
)
from .import_bindings import (
    is_loaded_trusted_module,
    loaded_trusted_module,
    plan_import,
)
from .library_origins import validate_library_module
from .registry import HandlerRegistry
from .values import (
    _MISSING,
    BoundMethod,
    BuiltinFunction,
    LambdaFunction,
    RowClass,
    Scope,
    TrustedCallable,
    TrustedModule,
    UserFunction,
    marshal_in,
    marshal_out,
    type_name,
)

#: Cap on *evaluated nodes*. The subset has no ``while`` and no unbounded
#: ``range``, so this bounds how many steps a program takes -- but not how much
#: work a single step does: ``2 ** (10 ** 9)`` is a handful of nodes and
#: unbounded time. Bounding work as well as node count needs operand-magnitude
#: limits on ``**``/``*``/``<<``, which this prototype does not have.
DEFAULT_STEP_BUDGET = 1_000_000

#: The only names ``import clauz3`` exposes to an interpreted program.
CLAUZ3_SURFACE = frozenset({"guarantee", "Row"})

#: Modelled library functions whose return value can never enter the value
#: domain, so exposing them would only defer the refusal to ``marshal_in``.
#: ``re.compile`` returns a ``Pattern`` and ``re.match``/``re.fullmatch`` return
#: ``Match`` -- live objects. The prover models all three, so this is a real
#: prove/run asymmetry: provable, not runnable. Refusing at the name says so
#: plainly instead of failing deep inside the boundary.
LIBRARY_RETURNS_OUTSIDE_DOMAIN = frozenset({"re.compile", "re.match", "re.fullmatch"})


def _provider_snapshot(module: ModuleType) -> ModuleType:
    """Capture a framework-selected provider without any dynamic attributes."""
    provider = ModuleType(module.__name__)
    provider.__dict__.update(
        (name, value)
        for name, value in module.__dict__.items()
        if not name.startswith("_")
    )
    return provider


# These providers are selected while the framework initializes, before an
# entry supplies import roots. An entry's imports never initialize a library
# or acquire an implementation through a mutable cached module's attributes.
_LIBRARY_PROVIDERS = {
    name: _provider_snapshot(module)
    for name, module in {
        "builtins": builtins,
        "clauz3": clauz3,
        "math": math,
        "ntpath": ntpath,
        "posixpath": posixpath,
        "os.path": ntpath if sys.platform == "win32" else posixpath,
    }.items()
}


#: One trusted call the program made: ``(qualname, args, kwargs)``.
Effect = tuple[str, tuple[Any, ...], dict[str, Any]]

#: Replacement for the real trusted call: ``(qualname, func, args, kwargs)``.
TrustedDispatch = Callable[
    [str, Callable[..., Any], "Sequence[Any]", "dict[str, Any]"], Any
]


class _Return(Exception):  # noqa: N818 - control flow, not an error
    """Non-local exit carrying a return value."""

    def __init__(self, value: Any) -> None:
        super().__init__()
        self.value = value


@dataclass
class Frame:
    """Per-call interpreter state."""

    program: Program
    scope: Scope
    #: Definitions currently on the call stack, held by *node identity*. The
    #: prover refuses to reason about recursion, so the interpreter refuses to
    #: run it. Identity rather than name because every ``lambda`` is called
    #: ``<lambda>``: keying on the name would let ``f = lambda n: f(n - 1)``
    #: recurse freely while falsely rejecting two distinct lambdas that call
    #: each other.
    trace: tuple[astroid.NodeNG, ...] = ()
    #: True while executing module level, i.e. *outside* the proved function
    #: body. Everything evaluated here is unproved, so the subset shrinks to
    #: inert declarations. This is a soundness boundary, not a containment one.
    module_scope: bool = False
    #: Every name this function body assigns anywhere, from astroid's own scope
    #: analysis. Python decides local-vs-enclosing statically, per function, not
    #: by walking scopes at each read -- so without this a read that precedes an
    #: assignment in the same body resolves to the enclosing binding instead of
    #: raising ``UnboundLocalError``, and ``x += 1`` in a nested function
    #: silently shadows rather than failing. Both are wrong *answers*, not
    #: refusals, which is the one failure mode this design cannot tolerate.
    local_names: frozenset[str] = frozenset()
    #: ``(scope, local_names)`` for every **lexically** enclosing body,
    #: innermost first. A nested body reads an enclosing local through the
    #: closure chain, and that chain walks straight past an enclosing ``Scope``
    #: layer with no binding yet -- landing on the module global and returning a
    #: wrong answer where CPython raises.
    #:
    #: Lexical, not dynamic. An earlier version derived this from the *calling*
    #: frame, so a module-level helper invoked from a body that later assigns
    #: the same name inherited an unrelated frame's locals and refused a program
    #: CPython runs happily. Scope in Python is decided at the definition site;
    #: the chain is captured there, on ``UserFunction``/``LambdaFunction``.
    enclosing: tuple[tuple[Scope, frozenset[str]], ...] = ()

    def nested(self) -> tuple[tuple[Scope, frozenset[str]], ...]:
        """The lexical chain a body *defined inside* this frame should carry."""
        if not self.local_names:
            return self.enclosing
        return ((self.scope, self.local_names), *self.enclosing)

    def unbound_local(self, name: str) -> bool:
        """True when ``name`` is statically local to a body that has not bound it."""
        if name in self.local_names and self.scope.get_own(name) is _MISSING:
            return True
        return any(
            name in names and scope.get_own(name) is _MISSING
            for scope, names in self.enclosing
        )

    def child(
        self,
        *,
        scope: Scope,
        node: astroid.NodeNG,
        local_names: frozenset[str] = frozenset(),
        enclosing: tuple[tuple[Scope, frozenset[str]], ...] = (),
    ) -> Frame:
        return Frame(
            program=self.program,
            scope=scope,
            trace=(*self.trace, node),
            local_names=local_names,
            enclosing=enclosing,
            # Carried, not reset: a callee reached from module level is still
            # outside the proved body. Nothing can originate a call at module
            # scope today, so this is defence in depth rather than a live path.
            module_scope=self.module_scope,
        )

    def recursing_into(self, node: astroid.NodeNG) -> bool:
        return any(entry is node for entry in self.trace)

    def step(self) -> None:
        self.program.steps += 1
        if self.program.steps <= self.program.step_budget:
            return
        # Name what already happened. Exhaustion aborts a proved, approved
        # program *mid-run*, so an arbitrary prefix of its trusted effects has
        # already fired; reporting only "exceeded step budget" left the
        # operator unable to tell a partial run from a refused one.
        done = [qualname for qualname, _args, _kwargs in self.program.effects]
        performed = (
            f"; effects already performed: {', '.join(done)}"
            if done
            else "; no effects had been performed"
        )
        raise ExecutionLimitExceeded(
            f"exceeded step budget of {self.program.step_budget}{performed}",
        )


eval_expr: HandlerRegistry[Any] = HandlerRegistry("expression")
eval_stmt: HandlerRegistry[None] = HandlerRegistry("statement")


# --------------------------------------------------------------------------
# Program driver
# --------------------------------------------------------------------------


@dataclass
class Program:
    """One interpreted module: its globals, its import policy, its budget."""

    source_name: str
    policy: ImportPolicy
    import_roots: tuple[Path, ...] = ()
    step_budget: int = DEFAULT_STEP_BUDGET
    steps: int = 0
    globals: Scope = field(default_factory=Scope)
    #: Module checks need bindings and the supported name surface, not library
    #: initialization. Never execute an agent-selected nontrusted import here.
    static_imports: bool = False
    #: Optional interception of the trusted boundary. Every call out to CPython
    #: goes through exactly one place, so a dry run, an audit log, or a
    #: record/replay harness is a parameter rather than a patched import.
    trusted_dispatch: TrustedDispatch | None = None
    #: Trusted calls made so far, in order: ``(qualname, args, kwargs)``.
    effects: list[Effect] = field(default_factory=list)

    def resolve_module(self, name: str) -> TrustedModule:
        """Import a module on behalf of the interpreted program.

        Import resolution lives here rather than in a patched ``__import__``:
        an interpreted program cannot call ``__import__`` because the name does
        not exist in the value domain, so there is no hook to bypass.
        """
        if not self.policy.is_allowed(name):
            raise InterpError(
                f"{self.source_name}: import of {name!r} is not in the allowlist",
            )
        try:
            return self._resolve_allowed_module(name)
        except ImportError as exc:
            raise TrustedBoundaryError(
                f"{self.source_name}: cannot resolve import {name!r}: {exc}"
            ) from exc

    def _resolve_allowed_module(self, name: str) -> TrustedModule:
        if not _matches_trusted_prefix(name, self.policy):
            validate_library_module(name, import_roots=self.import_roots)
            allowed, withheld = _library_surface(name)
            if name == "clauz3":
                allowed = CLAUZ3_SURFACE
            allowed -= withheld
            provider = _LIBRARY_PROVIDERS.get(name)
            available = frozenset() if provider is None else frozenset(vars(provider))
            unavailable = allowed - available
            return TrustedModule(
                qualname=name,
                module=None if self.static_imports else provider,
                allowed=allowed - unavailable,
                withheld=withheld,
                unavailable=unavailable,
            )
        module = loaded_trusted_module(
            name,
            trusted_roots=self.policy.trusted_roots,
            import_roots=self.import_roots,
        )
        if module is None:
            return TrustedModule(qualname=name, module=None)
        return TrustedModule(
            qualname=name,
            module=module,
            allowed=exposed_names(module, name, self.policy),
            withheld=withheld_names(name, self.policy),
            trusted=_is_trusted_layer(name, self.policy),
        )


def validate_import_origins(
    source: str,
    *,
    source_name: str,
    policy: ImportPolicy,
    import_roots: Iterable[Path],
    checked_trusted: bool = False,
) -> None:
    """Check every import declaration without executing an initializer.

    The first pass precedes authorized trusted initialization and checks all
    nontrusted names, including imports inside unused functions. The second
    pass also binds trusted imports from the checked layer's existing cache.
    This catches an invalid body import before any entry effect can occur.
    Missing fixed providers do not invalidate an unused namespace import.
    Their member bindings and reads are checked throughout the same source,
    including unused helpers, before proof or approval can admit execution.
    """
    program = Program(
        source_name=source_name,
        policy=policy,
        import_roots=tuple(Path(root) for root in import_roots),
        static_imports=True,
    )
    module = astroid.parse(source, path=source_name)
    incomplete: dict[
        str, list[tuple[astroid.Import | astroid.ImportFrom, TrustedModule]]
    ] = {}
    for node in module.nodes_of_class((astroid.Import, astroid.ImportFrom)):
        if isinstance(node, astroid.Import):
            for name, alias in node.names:
                if checked_trusted or not _matches_trusted_prefix(name, policy):
                    imported = program.resolve_module(name)
                    if imported.unavailable:
                        incomplete.setdefault(alias or name.split(".")[0], []).append(
                            (node, imported)
                        )
                _require_import_form(name, alias, source_name=source_name)
            continue
        if node.modname == "__future__":
            continue
        if node.level:
            raise UnsupportedNode("relative imports are not in the subset")
        if not checked_trusted and _matches_trusted_prefix(node.modname, policy):
            continue
        imported = program.resolve_module(node.modname)
        for name, alias in node.names:
            if name == "*":
                raise UnsupportedNode("star imports are not in the subset")
            imported.require_provider(name)
            if name in imported.allowed or name in imported.withheld:
                continue
            # This pass checks origins even inside unused functions. Binding
            # a member here would also reject an unused, unsupported member
            # such as re.match. Only a real child module adds another origin
            # to validate; actual import execution retains the surface check.
            child = f"{node.modname}.{name}"
            try:
                plan_import(child, import_roots=program.import_roots)
            except ModuleNotFoundError:
                continue
            child_module = program.resolve_module(child)
            if child_module.unavailable:
                incomplete.setdefault(alias or name, []).append((node, child_module))
    _require_library_provider_reads(module, incomplete)


def _require_library_provider_reads(
    module: astroid.Module,
    incomplete: dict[
        str, list[tuple[astroid.Import | astroid.ImportFrom, TrustedModule]]
    ],
) -> None:
    """Reject missing providers before an imported namespace can escape.

    Inspect reads rather than calls: aliases, returns, and argument forwarding
    all read the original namespace or member first. Lexical import bindings
    keep unrelated local variables with the same spelling independent. All
    candidate imports are checked, including duplicate aliases in one import.
    This is a conservative source gate, not registry isolation (issue #111).
    """
    for node in module.nodes_of_class(astroid.Name):
        candidates = incomplete.get(node.name, ())
        if not candidates:
            continue
        _, bindings = node.lookup(node.name)
        for declaration, imported in candidates:
            if bindings and all(binding is not declaration for binding in bindings):
                continue
            parent = node.parent
            if isinstance(parent, astroid.Attribute) and parent.expr is node:
                imported.require_provider(parent.attrname)
            else:
                # A partially supported namespace passed or saved as a value
                # could hide a later unavailable read from this static gate.
                imported.require_provider()


@dataclass(frozen=True)
class Outcome:
    """What an interpreted run produced."""

    value: Any
    #: Every trusted call the program made, in order. The interpreter observes
    #: the real effect sequence rather than inferring it, so it can be compared
    #: against the effects the proof reasoned about.
    effects: tuple[tuple[str, tuple[Any, ...], dict[str, Any]], ...]
    steps: int


def _matches_trusted_prefix(name: str, policy: ImportPolicy) -> bool:
    """Whether a name claims a trusted prefix, before checking its origin.

    Reads ``trusted_prefixes``, not ``allowed_prefixes``. The latter also holds
    dotted ``--allow-import`` entries, which grant *importability* to a module
    outside every trusted root; treating those as trusted layers would hand
    them a full ``TrustedCallable`` surface with no effect markers at all, in
    contradiction of the trust-boundary rule in AGENTS.md.
    """
    return any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in policy.trusted_prefixes
    )


def _is_trusted_layer(name: str, policy: ImportPolicy) -> bool:
    """Trust requires a matching name and a file inside a configured root."""
    return _matches_trusted_prefix(name, policy) and is_loaded_trusted_module(
        name, policy.trusted_roots
    )


def exposed_names(
    module: ModuleType,
    name: str,
    policy: ImportPolicy,
) -> frozenset[str]:
    """Which attributes of an imported module an interpreted program may name.

    This is the interpreted program's *entire* reachable surface outside its own
    source, so the rule is enumerate-what-is-in, never filter-what-is-out.

    A *trusted layer* exposes what it **defines** -- functions and ``Row``
    schemas whose ``__module__`` is this module -- plus subset constants. It
    does not expose what it *imports*. Python module attributes are transitive:
    ``import os`` at the top of ``effects.py`` makes ``effects.os`` an attribute,
    and exposing it would put ``effects.os.system(...)`` one access away from an
    interpreted program, defeating the whole design through a trusted layer that
    did nothing wrong. See ``test_interp_escapes.py::
    test_trusted_layer_imports_are_not_its_surface``.

    A *library* module from the stdlib allowlist is narrower still: only the
    functions the prover models in ``deal_solver._funcs.FUNCTIONS``. Without
    this, ``math.sqrt`` and ``json.loads`` would execute at runtime although the
    prover has no model for them -- the import allowlist admits 13 stdlib
    modules, of which the prover models three.
    """
    if name == "clauz3":
        return CLAUZ3_SURFACE
    if _is_trusted_layer(name, policy):
        return frozenset(
            attr
            for attr in dir(module)
            if not attr.startswith("_") and _is_trusted_definition(module, attr, policy)
        )
    modelled, withheld = _library_surface(name)
    return modelled - withheld


def withheld_names(name: str, policy: ImportPolicy) -> frozenset[str]:
    """Names the prover models on ``name`` that the boundary cannot marshal.

    Separate from :func:`exposed_names` so the refusal can say *which* of the
    two reasons applies. ``re`` has an empty surface because all three modelled
    functions return live ``Pattern``/``Match`` objects -- not because the
    prover models nothing, which is what the diagnostic used to claim and which
    points the reader at a fix that cannot work.
    """
    if name == "clauz3" or _is_trusted_layer(name, policy):
        return frozenset()
    _, withheld = _library_surface(name)
    return withheld


def _library_surface(name: str) -> tuple[frozenset[str], frozenset[str]]:
    """``(modelled, withheld)`` attribute names for a library module."""
    from clauz3._vendor.deal_solver._funcs import FUNCTIONS

    prefix = name + "."
    modelled = frozenset(
        key[len(prefix) :]
        for key in FUNCTIONS
        if key.startswith(prefix) and "." not in key[len(prefix) :]
    )
    withheld = frozenset(
        key[len(prefix) :]
        for key in LIBRARY_RETURNS_OUTSIDE_DOMAIN
        if key.startswith(prefix)
    )
    return modelled, withheld


def _imported_names(module: ModuleType) -> frozenset[str] | None:
    """Names an ``import`` statement binds in ``module``, read from its source.

    ``None`` means *provenance unknown*, which callers must treat as "admit
    nothing". Both failure paths used to return an empty set, which
    ``_is_trusted_definition`` reads as "nothing was imported" and therefore
    "every constant here is locally defined" -- fail-open, in the one check
    standing between a trusted layer's ``from myapp.settings import API_TOKEN``
    and the interpreted surface. A zipimport or frozen loader, a namespace
    package, or a source-less ``.pyc`` deployment all reach it. Every other
    admission rule in this package enumerates what is in; this one defaulted to
    admitting on error.
    """
    path = getattr(module, "__file__", None)
    if path is None:
        return None
    try:
        source = Path(path).read_text()
    except OSError:  # pragma: no cover - defensive
        return None
    return _import_bindings(source)


@functools.lru_cache(maxsize=128)
def _import_bindings(source: str) -> frozenset[str] | None:
    r"""Names the ``import`` statements in ``source`` bind; ``None`` if unparsable.

    Memoised on the source *text*, not the module object. ``exposed_names``
    reaches :func:`_imported_names` once per public constant of a trusted
    layer, on every ``resolve_module``, and each call re-parsed the whole file.
    Keying on the text keeps the answer exact under ``importlib.reload`` (the
    file is re-read every time, so an edited layer is a different key) and
    retains no module objects; the bound keeps the cache from growing with
    the process.

    >>> sorted(_import_bindings("import os.path\nfrom a import b as c\nX = 1\n"))
    ['c', 'os']
    >>> _import_bindings("import (") is None
    True
    """
    try:
        tree = astroid.parse(source)
    except astroid.AstroidSyntaxError:  # pragma: no cover - defensive
        return None
    bound: set[str] = set()
    for statement in tree.nodes_of_class((astroid.Import, astroid.ImportFrom)):
        for name, alias in statement.names:
            bound.add(alias or name.split(".", 1)[0])
    return frozenset(bound)


def _is_locally_defined(module: ModuleType, attr: str) -> bool:
    """True when ``attr`` is bound by this module rather than imported into it.

    ``None`` from :func:`_imported_names` means the source could not be read,
    so provenance is unknown and the answer is no.
    """
    imported = _imported_names(module)
    if imported is None:
        return False
    return attr not in imported


def _is_trusted_definition(
    module: ModuleType,
    attr: str,
    policy: ImportPolicy,
) -> bool:
    """True when ``module.attr`` was defined inside the *trusted layer*.

    Not merely inside this module: a layer legitimately splits across files and
    re-exports (``from tools.db.trusted.schemas import UserRow`` in
    ``effects.py``, which ships in ``examples/email-from-db``). Requiring
    ``__module__ == module.__name__`` dropped those, so a program importing the
    schema from the module that re-exports it was refused although it proves.

    A re-export's origin must be a loaded file physically inside a configured
    trusted root as well as under its dotted prefix. Namespace packages can
    include unrelated directories, so the prefix alone cannot establish this.
    Modules never count, whatever their origin.

    Note the dependency on ``functools.wraps``: ``@deal.has`` and ``@deal.pre``
    preserve ``__module__``, which is why decorated effects stay visible. A
    trusted-layer decorator that did not would silently empty its surface.
    """
    value = getattr(module, attr, None)
    if isinstance(value, ModuleType):
        return False
    if value is None or isinstance(value, (bool, int, float, str)):
        # A scalar has no ``__module__`` to check, so provenance comes from the
        # source: ``from myapp.settings import API_TOKEN`` inside a layer would
        # otherwise put a constant from outside every trusted root into the
        # interpreted surface, contradicting "exposes what it defines".
        return _is_locally_defined(module, attr)
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        # Same rule one type up, and missing until review round 12. A builtin
        # container has no ``__module__`` either -- ``getattr([1], "__module__",
        # None)`` is ``None`` -- so the ``origin is None`` branch below dropped
        # every ``ALLOWED = ["bob@example.com"]`` a trusted layer defines. The
        # prover reads such a constant fine (``eval_attr`` infers the ``List``
        # node and evaluates it), so ``if addr in effects.ALLOWED:`` **proved**
        # and was then refused at run -- after the approval round trip and
        # after the receipt nonce was consumed. It also contradicted this
        # module's own docstring, which promises "plus subset constants".
        #
        # ``marshal_in`` is the admission test because it is exactly the
        # predicate that decides whether a value may cross the boundary, and it
        # recurses: a list holding a live object is refused here rather than
        # deferred to the first read.
        if not _is_locally_defined(module, attr):
            return False
        try:
            marshal_in(value, origin=f"{module.__name__}.{attr}")
        except TrustedBoundaryError:
            return False
        return True
    origin = getattr(value, "__module__", None)
    if origin is None:
        return False
    if origin == module.__name__:
        return True
    return _is_trusted_layer(origin, policy)


@contextmanager
def _program_layer(source: str, program: Program) -> Iterator[None]:
    """Keep checked source, registrations and lazy imports in one selection."""
    from clauz3.prover import (
        ProverConfigError,
        _selected_layer_context,
        _trusted_layer_context,
    )

    roots = list(program.policy.trusted_roots)
    paths = list(program.import_roots)
    try:
        with _selected_layer_context(roots, paths):
            validate_import_origins(
                source,
                source_name=program.source_name,
                policy=program.policy,
                import_roots=paths,
            )
            with _trusted_layer_context(roots, paths):
                yield
    except ProverConfigError as exc:
        raise TrustedBoundaryError(str(exc)) from exc


def execute(
    source: str,
    *,
    source_name: str,
    policy: ImportPolicy,
    target: str = "main",
    import_roots: Iterable[Path] = (),
    step_budget: int = DEFAULT_STEP_BUDGET,
    trusted_dispatch: TrustedDispatch | None = None,
    effect_log: list[Effect] | None = None,
) -> Outcome:
    """Interpret ``source`` and call ``target()``.

    Raises :class:`InterpError` (or a subclass) for anything the subset does
    not admit, and :class:`ProgramError` if the program itself raises.

    ``effect_log``, when given, receives each trusted call as it is made, so a
    caller still knows what fired when the run raises instead of returning an
    :class:`Outcome`. A ``step_budget`` below 1 could never run anything and
    is refused with ``ValueError`` before module level runs.
    """
    if step_budget < 1:
        raise ValueError(f"step_budget must be at least 1, got {step_budget}")
    program = Program(
        source_name=source_name,
        policy=policy,
        import_roots=tuple(Path(root) for root in import_roots),
        step_budget=step_budget,
        trusted_dispatch=trusted_dispatch,
        effects=[] if effect_log is None else effect_log,
    )
    # Selection spans the actual entry call, including lazy imports inside
    # trusted effects. Another proof cannot swap the layer halfway through.
    with _program_layer(source, program):
        _load_module(source, source_name=source_name, program=program)
        func = _resolve_target(program, target=target, source_name=source_name)
        # Entering the target is exactly where module scope ends: everything up
        # to here ran unproved, everything from here is what the proof covers.
        # The module frame is deliberately not reused -- ``Frame.child`` carries
        # ``module_scope``, so calling through it would apply the module-level
        # inertness rule to the proved body.
        entry = Frame(program=program, scope=program.globals)
        value = call(func, args=[], kwargs={}, frame=entry)
    return Outcome(
        value=value,
        effects=tuple(program.effects),
        steps=program.steps,
    )


def target_node(
    source: str,
    *,
    target: str,
    source_name: str,
    policy: ImportPolicy,
    import_roots: Iterable[Path] = (),
) -> astroid.FunctionDef:
    """The exact definition the interpreter *will* execute for ``target``.

    Used by ``runner._preflight_structure`` to validate module loading and
    target resolution before requesting approval. The target is resolved but
    never called here.

    Deliberately shares :func:`_load_module` and :func:`_resolve_target` with
    :func:`execute` rather than re-deriving the answer. An earlier version
    scanned ``module.body`` for the last ``FunctionDef`` *named* ``target``,
    which is a different question from "what is ``target`` bound to" -- a
    rebinding (``main = sneaky``, ``from x import y as main``) made the two
    disagree, so wiring it to the prover would have proved one body and run
    another. Answering by re-running the same resolution is the only way this
    cannot drift.

    Entry ambiguity is rejected statically by ``clauz3.entry_bindings`` (#97).
    Selecting a theorem from this node alone would leave guarantee extraction
    and policy rewriting free to select a different definition. Requiring a
    unique direct entry binding keeps all of those consumers in agreement.

    File-backed trusted imports must already be loaded by the checked trusted
    layer context. This static pass never imports an entry-selected module.
    """
    program = Program(
        source_name=source_name,
        policy=policy,
        import_roots=tuple(Path(root) for root in import_roots),
        static_imports=True,
    )
    with _program_layer(source, program):
        _load_module(source, source_name=source_name, program=program)
        return _resolve_target(program, target=target, source_name=source_name).node


def check_module_level(
    source: str,
    *,
    source_name: str,
    policy: ImportPolicy,
    import_roots: Iterable[Path] = (),
) -> None:
    """Run module level under the inert-declaration gates, and stop there.

    The prover walks the target's body; :func:`_exec_module` is the rule about
    everything outside it. ``prover.validate_module_level`` calls this so
    ``clauz3 prove`` refuses a module-level effect, a module-level function's
    effectful default, or a bare call the same way ``clauz3 run`` does (issue
    #100). Sharing the executor's gate keeps their module-level rules aligned.
    The entry target is not resolved or called. Nontrusted imports bind only
    inert names, so an allowed name cannot execute an agent-authored module
    during checking. Trusted files must already be loaded by the checked
    trusted-layer context; namespace containers are inert bindings as well.
    """
    program = Program(
        source_name=source_name,
        policy=policy,
        import_roots=tuple(Path(root) for root in import_roots),
        static_imports=True,
    )
    with _program_layer(source, program):
        _load_module(source, source_name=source_name, program=program)


def _load_module(source: str, *, source_name: str, program: Program) -> Frame:
    """Parse and execute module level, returning the module frame."""
    validate_import_origins(
        source,
        source_name=source_name,
        policy=program.policy,
        import_roots=program.import_roots,
        checked_trusted=True,
    )
    module = astroid.parse(source, path=source_name)
    frame = Frame(program=program, scope=program.globals, module_scope=True)
    _exec_module(module, frame)
    return frame


def _resolve_target(
    program: Program,
    *,
    target: str,
    source_name: str,
) -> UserFunction:
    """The callable ``target`` is bound to after module level has run."""
    func = program.globals.get(target)
    if func is _MISSING:
        raise NameResolutionError(f"{source_name}: no definition named {target!r}")
    if not isinstance(func, UserFunction):
        raise InterpError(f"{source_name}: target {target!r} is not a function")
    return func


def _exec_module(module: astroid.Module, frame: Frame) -> None:
    """Execute module level.

    Only *inert declarations* run here. This is a soundness rule, not a
    containment rule: the proof covers the target function's body, so anything
    evaluated out here executes unproved. Containment is handled by the subset
    itself, but containment is not the question -- a trusted effect reached from
    module level is perfectly contained and still unproved.

    Concretely, assignment values, assignment targets and default arguments
    must all be inert. That mirrors ``runner._validate_module_level`` but is
    not identical to it. The interpreter is the more permissive side by exactly
    two inert cases:

    - a signed numeric literal (``OFFSET = -3``), which CPython parses as a
      ``UnaryOp`` and ``runner._is_constant_expr`` therefore rejects; PR #106
      moves ``runner`` the same way;
    - a bare module-level ``pass``, which ``runner``'s catch-all rejects.

    Both evaluate nothing, so neither is a soundness difference -- but this
    docstring exists to say the two gates are *not* interchangeable, and that
    only means anything if the enumeration is exact.

    An earlier version of this prototype evaluated values and defaults with
    the full expression evaluator, on the reasoning that "the interpreter
    evaluates it, so it is just more interpreted code" -- which confuses the two
    rules. It does not matter *who* evaluates an effect outside the proved body;
    it matters that it is outside.
    """
    for stmt in module.body:
        if isinstance(
            stmt,
            (
                astroid.Import,
                astroid.ImportFrom,
                astroid.FunctionDef,
                astroid.ClassDef,
                astroid.Pass,
                astroid.Assign,
                astroid.AnnAssign,
            ),
        ):
            eval_stmt(stmt, frame)
            continue
        if _is_docstring(stmt):
            continue
        raise UnsupportedNode(
            f"{frame.program.source_name}: module-level "
            f"{type(stmt).__name__} would run outside the proved function",
        )


def _unpack(targets: list[astroid.NodeNG], values: list[Any], frame: Frame) -> None:
    """Destructure ``values`` across ``targets``, honouring one ``*rest``."""
    starred = [i for i, t in enumerate(targets) if isinstance(t, astroid.Starred)]
    if len(starred) > 1:
        raise ProgramError(
            "multiple starred expressions in assignment", kind="SyntaxError"
        )
    if not starred:
        if len(targets) != len(values):
            raise ProgramError(
                f"cannot unpack {len(values)} values into {len(targets)} targets",
                kind="ValueError",
            )
        for element, item in zip(targets, values, strict=True):
            _assign_to(element, item, frame)
        return
    index = starred[0]
    after = len(targets) - index - 1
    if len(values) < len(targets) - 1:
        raise ProgramError(
            f"not enough values to unpack (expected at least {len(targets) - 1}, "
            f"got {len(values)})",
            kind="ValueError",
        )
    for element, item in zip(targets[:index], values[:index], strict=True):
        _assign_to(element, item, frame)
    rest = values[index : len(values) - after]
    _assign_to(targets[index].value, rest, frame)
    tail_targets = targets[index + 1 :]
    for element, item in zip(tail_targets, values[len(values) - after :], strict=True):
        _assign_to(element, item, frame)


def _require_inert_target(target: astroid.NodeNG, frame: Frame) -> None:
    """Refuse a module-level assignment *target* that evaluates expressions.

    Gating only the value is not enough. ``CFG[send_email(...)] = 1`` has a
    constant value and a subscript target, and ``_assign_to`` evaluates both
    ``target.value`` and ``target.slice`` -- so the trusted call fires at module
    level, before the proved function is entered. That was live in the first
    version of this gate, and ``runner._validate_module_level`` has the same
    blind spot on the ``exec`` path.

    Plain names, and tuple/list destructuring of plain names, evaluate nothing.
    """
    if not frame.module_scope:
        return
    if _is_inert_target(target):
        return
    raise UnsupportedNode(
        f"{frame.program.source_name}: module-level assignment target "
        f"{target.as_string()!r} must be a name, or destructuring of names; "
        "any other target evaluates subexpressions outside the proved function",
    )


def _is_inert_target(target: astroid.NodeNG) -> bool:
    """True for targets that evaluate nothing: names and destructuring of names.

    ``Starred`` is unwrapped -- ``A, *B = (1, 2, 3)`` binds names and evaluates
    no subexpression. ``runner._is_inert_target`` says the same thing about the
    ``exec`` path; both had to learn it.
    """
    if isinstance(target, astroid.AssignName):
        return True
    if isinstance(target, astroid.Starred):
        return _is_inert_target(target.value)
    if isinstance(target, (astroid.Tuple, astroid.List)):
        return all(_is_inert_target(element) for element in target.elts)
    return False


def _require_literal(node: astroid.NodeNG, frame: Frame, what: str) -> None:
    """Refuse a non-literal expression in a position the prover never evaluates.

    Unlike :func:`_require_inert` this does not depend on ``module_scope``: the
    position is unproved wherever it appears.
    """
    if _is_constant_expr(node):
        return
    raise UnsupportedNode(
        f"{frame.program.source_name}: {what} must be a constant expression; "
        f"the prover never evaluates it, so {node.as_string()!r} would run "
        "unproved",
    )


def _require_inert(node: astroid.NodeNG, frame: Frame, what: str) -> None:
    """Refuse a module-level expression that is not a literal.

    Only called when ``frame.module_scope`` is set, so the same expression
    inside a proved function body stays unrestricted.
    """
    if not frame.module_scope or _is_constant_expr(node):
        return
    raise UnsupportedNode(
        f"{frame.program.source_name}: module-level {what} must be a constant "
        f"expression; {node.as_string()!r} would evaluate outside the proved "
        "function",
    )


def _is_constant_expr(node: astroid.NodeNG) -> bool:
    """Literals, signed numeric literals, and literal containers thereof."""
    if isinstance(node, astroid.Const):
        return True
    if isinstance(node, astroid.UnaryOp) and node.op in {"-", "+"}:
        return _is_constant_expr(node.operand)
    if isinstance(node, (astroid.List, astroid.Tuple, astroid.Set)):
        return all(_is_constant_expr(item) for item in node.elts)
    if isinstance(node, astroid.Dict):
        return all(
            _is_constant_expr(key) and _is_constant_expr(value)
            for key, value in node.items
        )
    return False


def _is_docstring(stmt: astroid.NodeNG) -> bool:
    return isinstance(stmt, astroid.Expr) and isinstance(stmt.value, astroid.Const)


# --------------------------------------------------------------------------
# Calls
# --------------------------------------------------------------------------


def call(
    value: Any,
    *,
    args: Sequence[Any],
    kwargs: dict[str, Any],
    frame: Frame,
) -> Any:
    """Apply a callable value."""
    frame.step()

    if isinstance(value, TrustedCallable):
        # The one hand-off to CPython. Trusted layers are full Python; only the
        # values are constrained.
        out_args = [marshal_out(arg, origin=value.qualname) for arg in args]
        out_kwargs = {
            key: marshal_out(val, origin=value.qualname) for key, val in kwargs.items()
        }
        if value.trusted:
            # Only trusted-layer calls are effects. ``math.sin`` and
            # ``clauz3.guarantee`` reach CPython through the same branch, but
            # recording them here would put entries in ``Outcome.effects`` that
            # the proof's effect reasoning never concerned itself with, which
            # is exactly the comparison this list exists to support.
            frame.program.effects.append(
                (value.qualname, tuple(out_args), dict(out_kwargs))
            )
        dispatch = frame.program.trusted_dispatch
        if dispatch is None:
            result = _apply_boundary(value, out_args, out_kwargs)
        else:
            result = dispatch(value.qualname, value.func, out_args, out_kwargs)
        return marshal_in(result, origin=value.qualname)

    if isinstance(value, BoundMethod):
        try:
            return _require_in_domain(value.impl(value.receiver, *args, **kwargs))
        except (TypeError, ValueError, ArithmeticError, KeyError, IndexError) as exc:
            raise ProgramError(
                f"{type_name(value.receiver)}.{value.name}(): {exc}",
                kind=type(exc).__name__,
            ) from exc

    if isinstance(value, BuiltinFunction):
        try:
            return value.impl(*args, **kwargs)
        except (TypeError, ValueError, ArithmeticError) as exc:
            # ``ord('ab')``, ``min()``, ``int(1e999)`` -- the program called a
            # subset builtin wrongly. Report it as a program fault so callers
            # keying "non-OK" off InterpError see a consistent type.
            raise ProgramError(
                f"{value.name}(): {exc}", kind=type(exc).__name__
            ) from exc

    if isinstance(value, RowClass):
        if args:
            raise UnsupportedNode(
                f"{value.name}() takes keyword arguments only in the subset",
            )
        try:
            return value.schema(**kwargs)
        except TypeError as exc:
            # ``Row.__init__`` reports missing/unknown fields. That is an
            # ordinary program bug, the same class as a failed assert.
            raise ProgramError(f"{value.name}(): {exc}", kind="TypeError") from exc

    if isinstance(value, UserFunction):
        if frame.recursing_into(value.node):
            raise UnsupportedNode(
                f"recursive call to {value.name}() is not in the proved subset",
            )
        scope = _bind_arguments(
            value.node.args,
            args=args,
            kwargs=kwargs,
            defaults=value.defaults,
            closure=value.closure,
            what=f"{value.name}()",
        )
        inner = frame.child(
            scope=scope,
            node=value.node,
            local_names=frozenset(value.node.locals),
            enclosing=value.enclosing,
        )
        try:
            for stmt in value.node.body:
                eval_stmt(stmt, inner)
        except _Return as ret:
            return ret.value
        return None

    if isinstance(value, LambdaFunction):
        if frame.recursing_into(value.node):
            raise UnsupportedNode(
                "recursive call to a lambda is not in the proved subset",
            )
        scope = _bind_arguments(
            value.node.args,
            args=args,
            kwargs=kwargs,
            defaults=value.defaults,
            closure=value.closure,
            what="<lambda>()",
        )
        inner = frame.child(
            scope=scope,
            node=value.node,
            local_names=frozenset(value.node.locals),
            enclosing=value.enclosing,
        )
        return eval_expr(value.node.body, inner)

    raise UnsupportedNode(f"{type_name(value)} is not callable in the subset")


def _apply_boundary(
    value: TrustedCallable,
    args: Sequence[Any],
    kwargs: dict[str, Any],
) -> Any:
    """Call across the boundary, converting *library* faults to program faults.

    A **trusted layer** raising is a real failure of real code -- it opened a
    socket and the socket failed -- and propagates unchanged, because the
    interpreter has nothing useful to say about it and swallowing it would hide
    an effect that partly happened.

    A **modelled library** function is different. ``math.trunc(1e999)`` and
    ``math.isclose(1, 2, rel_tol=-1)`` are the program calling a subset
    primitive wrongly, exactly like ``ord('ab')`` -- and those go through
    ``BuiltinFunction`` and come back as ``ProgramError``. Left bare, the
    native ``OverflowError``/``ValueError`` escaped ``execute()`` past both
    ``except`` clauses in ``runner._execute_source`` to the CLI catch-all,
    contradicting the total-error contract this module documents and
    ``test_error_contract_is_total`` asserts.
    """
    if value.trusted:
        return value.func(*args, **kwargs)
    try:
        return value.func(*args, **kwargs)
    except (TypeError, ValueError, ArithmeticError, LookupError) as exc:
        raise ProgramError(
            f"{value.qualname}(): {exc}", kind=type(exc).__name__
        ) from exc


def _bind_arguments(
    arguments: astroid.Arguments,
    *,
    args: Sequence[Any],
    kwargs: dict[str, Any],
    defaults: dict[str, Any],
    closure: Scope,
    what: str,
) -> Scope:
    if arguments.vararg or arguments.kwarg:
        raise UnsupportedNode(f"{what}: *args/**kwargs are not in the proved subset")
    if getattr(arguments, "posonlyargs", None):
        raise UnsupportedNode(
            f"{what}: positional-only parameters are not in the proved subset",
        )
    if arguments.kwonlyargs:
        raise UnsupportedNode(
            f"{what}: keyword-only parameters are not in the proved subset",
        )
    names = [arg.name for arg in arguments.args or ()]
    scope = Scope(parent=closure)
    if len(args) > len(names):
        raise UnsupportedNode(f"{what}: too many positional arguments")
    for name, value in zip(names, args, strict=False):
        scope.set(name, value)
    for key, value in kwargs.items():
        if key not in names:
            raise UnsupportedNode(f"{what}: unexpected keyword argument {key!r}")
        if key in scope.names:
            raise UnsupportedNode(f"{what}: duplicate value for {key!r}")
        scope.set(key, value)
    for name in names:
        if name in scope.names:
            continue
        if name in defaults:
            scope.set(name, defaults[name])
            continue
        raise UnsupportedNode(f"{what}: missing argument {name!r}")
    return scope


# --------------------------------------------------------------------------
# Statements
# --------------------------------------------------------------------------


@eval_stmt.register(astroid.FunctionDef)
def _stmt_function_def(node: astroid.FunctionDef, frame: Frame) -> None:
    """Bind a definition.

    Decorators are checked by name and never evaluated. That single choice
    removes the whole "a decorator executes at import time" attack surface --
    there is no expression evaluation to exploit, so there is nothing for a
    validator to have to anticipate.
    """
    _check_decorators(node, frame)
    defaults = _eval_defaults(node.args, frame)
    frame.scope.set(
        node.name,
        UserFunction(
            node=node,
            closure=frame.scope,
            enclosing=frame.nested(),
            defaults=defaults,
        ),
    )


def _check_decorators(node: astroid.NodeNG, frame: Frame) -> None:
    """The only decorator in the subset is ``@clauz3.guarantee``.

    Matched by the target it resolves to, not by its source text, so this
    agrees with the prover about ``import clauz3 as c`` and about ``import json
    as clauz3`` -- issue #129 and :mod:`clauz3.decorators`. Recorded, never
    evaluated, so this stays a soundness rule rather than a sandbox one.
    """
    decorators = getattr(node, "decorators", None)
    if decorators is None:
        return
    aliases = decorators_mod.import_aliases(node.root())
    for decorator in decorators.nodes:
        if not decorators_mod.is_guarantee(decorator, aliases):
            spelled = decorators_mod.dotted_name(decorator) or decorator.as_string()
            raise UnsupportedNode(
                f"{frame.program.source_name}: decorator "
                f"@{spelled} is not part of the proved subset",
            )


def _eval_defaults(arguments: astroid.Arguments, frame: Frame) -> dict[str, Any]:
    """Evaluate default arguments at ``def`` time, as CPython does.

    Every default is restricted to a literal, at module level and inside a
    proved body alike -- see the comment on :func:`_require_literal` below for
    why the nested case is not the weaker one.
    """
    names = [arg.name for arg in arguments.args or ()]
    defaults = arguments.defaults or []
    if len(defaults) > len(names):
        raise UnsupportedNode(
            "positional-only parameters with defaults are not in the proved subset",
        )
    if any(node is not None for node in arguments.kw_defaults or ()):
        # ``runner._check_defaults`` covered these and this did not. Never
        # evaluated today (``_bind_arguments`` refuses keyword-only parameters
        # at call time), so it was fail-closed by accident rather than by rule.
        raise UnsupportedNode(
            "keyword-only defaults are not in the proved subset",
        )
    bound: dict[str, Any] = {}
    for name, node in zip(names[len(names) - len(defaults) :], defaults, strict=True):
        # Unconditional, not module-scope-gated. ``eval_func_def`` binds a
        # ``FuncSort`` and never touches ``node.args``, so the prover does not
        # evaluate a default *anywhere* -- not at module level, and not inside a
        # proved body either. An earlier version gated this on
        # ``frame.module_scope`` on the reasoning that "a nested def is reached
        # only along a proved path". Reachability along a proved path is not the
        # same as the expression having been proved:
        #
        #     @clauz3.guarantee(emails.only(["bob@example.com"]))
        #     def main() -> None:
        #         def helper(x=send_email("eve@example.com", "exfil")):
        #             ...
        #
        # proved OK -- the effect set the guarantee ranges over was empty -- and
        # then sent the mail. A false proof, from the same "every syntactic
        # position the evaluator can be entered from" lesson this design note
        # already records twice.
        _require_literal(node, frame, f"default for {name!r}")
        bound[name] = eval_expr(node, frame)
    return bound


@eval_stmt.register(astroid.ClassDef)
def _stmt_class_def(node: astroid.ClassDef, frame: Frame) -> None:
    """Build a ``clauz3.Row`` schema.

    The class body is read, not executed: field names and their declared types
    come from the annotations as *syntax*. There is no namespace to populate, no
    metaclass hook to fire, and no annotation expression to evaluate, so
    ``class C(metaclass=evil())`` has nothing to attach to.
    """
    if not frame.module_scope:
        # The prover registers no ``ClassDef`` statement handler, so a class
        # defined inside a proved body runs here and skips there -- the same
        # reason ``_stmt_ann_assign`` is module-scope only. Module-level Row
        # schemas are resolved prover-side by ``row_schema_from_class_def``.
        raise UnsupportedNode(
            f"{node.name}: a class inside a function body is not in the proved "
            "subset; declare Row schemas at module level",
        )
    _check_decorators(node, frame)
    if node.keywords:
        raise UnsupportedNode(
            f"{node.name}: class keywords (metaclass=...) are not in the subset",
        )
    bases = [base.as_string() for base in node.bases]
    if bases not in (["Row"], ["clauz3.Row"]):
        raise UnsupportedNode(
            f"{node.name}: only 'class X(Row)' schema declarations are in the subset",
        )
    fields: dict[str, str] = {}
    for stmt in node.body:
        if isinstance(stmt, astroid.Pass) or _is_docstring(stmt):
            continue
        if not isinstance(stmt, astroid.AnnAssign) or stmt.value is not None:
            raise UnsupportedNode(
                f"{node.name}: a Row body may only declare 'field: type'",
            )
        if not isinstance(stmt.target, astroid.AssignName):
            raise UnsupportedNode(f"{node.name}: unsupported field target")
        annotation = stmt.annotation.as_string()
        if annotation not in {"str", "int", "bool"}:
            raise UnsupportedNode(
                f"{node.name}.{stmt.target.name}: Row fields must be str/int/bool",
            )
        fields[stmt.target.name] = annotation
    schema = type(
        node.name,
        (Row,),
        {
            "__annotations__": {
                n: {"str": str, "int": int, "bool": bool}[t] for n, t in fields.items()
            }
        },
    )
    frame.scope.set(node.name, RowClass(schema=schema))


@eval_stmt.register(astroid.Import)
def _stmt_import(node: astroid.Import, frame: Frame) -> None:
    for name, alias in node.names:
        _require_import_form(name, alias, source_name=frame.program.source_name)
        if alias:
            frame.scope.set(alias, frame.program.resolve_module(name))
            continue
        frame.scope.set(name, frame.program.resolve_module(name))


def _require_import_form(name: str, alias: str | None, *, source_name: str) -> None:
    """Share the runtime import grammar with the whole-source preflight."""
    if alias is not None or "." not in name:
        return
    # ``import a.b.c`` binds the head package and needs attribute traversal,
    # which the interpreter does not support. Check body imports before any
    # entry effect too, whether or not a symbolic provider is registered.
    raise UnsupportedNode(
        f"{source_name}: 'import {name}' is not in the "
        f"proved subset; use 'from {name.rsplit('.', 1)[0]} import "
        f"{name.rsplit('.', 1)[1]}' or 'import {name} as <alias>'",
    )


@eval_stmt.register(astroid.ImportFrom)
def _stmt_import_from(node: astroid.ImportFrom, frame: Frame) -> None:
    if node.modname == "__future__":
        # A compiler pragma, not a value. ``runner._sanitize_for_exec`` injects
        # exactly this line into every executed module, and the interpreter
        # never evaluates an annotation, so accepting it as a no-op keeps a
        # program that proves and runs today running here too.
        return
    if node.level:
        raise UnsupportedNode("relative imports are not in the subset")
    module = frame.program.resolve_module(node.modname)
    for name, alias in node.names:
        if name == "*":
            raise UnsupportedNode("star imports are not in the subset")
        frame.scope.set(alias or name, _from_import(module, node.modname, name, frame))


def _from_import(
    module: TrustedModule,
    modname: str,
    name: str,
    frame: Frame,
) -> Any:
    """``from pkg import name`` where ``name`` may be a submodule.

    A submodule is not an attribute of its package until it has been imported,
    so fall back to resolving ``pkg.name`` as a module in its own right. That
    fallback goes through :meth:`Program.resolve_module`, so the submodule gets
    the allowlist check and its own computed surface -- unlike reading it off
    the parent as an attribute, which would inherit nothing and expose
    everything.
    """
    try:
        return module.attr(name)
    except TrustedBoundaryError as exc:
        try:
            return frame.program.resolve_module(f"{modname}.{name}")
        except (ImportError, InterpError):
            raise exc from None


@eval_stmt.register(astroid.Pass)
def _stmt_pass(node: astroid.Pass, frame: Frame) -> None:
    return None


@eval_stmt.register(astroid.Expr)
def _stmt_expr(node: astroid.Expr, frame: Frame) -> None:
    eval_expr(node.value, frame)


@eval_stmt.register(astroid.Assign)
def _stmt_assign(node: astroid.Assign, frame: Frame) -> None:
    _require_inert(node.value, frame, "assignment value")
    value = eval_expr(node.value, frame)
    for target in node.targets:
        _assign_to(target, value, frame)


@eval_stmt.register(astroid.AnnAssign)
def _stmt_ann_assign(node: astroid.AnnAssign, frame: Frame) -> None:
    """Annotated assignment. The annotation is never evaluated.

    Restricted to module scope, where it is a declaration (``LIMIT: int = 10``,
    or a ``Row`` field, which ``_stmt_class_def`` reads structurally without
    dispatching here). The prover registers no ``AnnAssign`` handler, so inside
    a proved body ``x: int = 1`` would run and be unprovable -- the shape this
    design exists to eliminate.
    """
    if not frame.module_scope:
        raise UnsupportedNode(
            "annotated assignment inside a function body is not in the proved "
            "subset; the prover has no handler for it -- use a plain assignment",
        )
    # Before the early return: CPython's ``compiler_annassign`` emits
    # ``check_ann_subscr`` for a value-less complex target, so ``CFG[f()]: int``
    # does evaluate ``f()`` under ``exec``. Nothing is evaluated here, but the
    # gate must cover the same syntactic positions the runner's now does.
    _require_inert_target(node.target, frame)
    if node.value is None:
        return
    _require_inert(node.value, frame, "assignment value")
    _assign_to(node.target, eval_expr(node.value, frame), frame)


def _assign_to(target: astroid.NodeNG, value: Any, frame: Frame) -> None:
    _require_inert_target(target, frame)
    if isinstance(target, astroid.AssignName):
        frame.scope.set(target.name, value)
        return
    if isinstance(target, astroid.Subscript):
        container = eval_expr(target.value, frame)
        if not isinstance(container, (list, dict)):
            raise UnsupportedNode(
                f"cannot assign into {type_name(container)} in the subset",
            )
        key = eval_expr(target.slice, frame)
        try:
            container[key] = value
        except (IndexError, TypeError) as exc:
            raise ProgramError(str(exc), kind=type(exc).__name__) from exc
        return
    if isinstance(target, (astroid.Tuple, astroid.List)):
        if not frame.module_scope:
            # ``_eval_stmt.eval_assign`` accepts only ``AssignName`` and
            # ``Subscript`` targets -- "cannot assign to Tuple". Node-type
            # parity cannot see this: ``Assign`` is registered on both sides,
            # and the difference lives inside the handler. Module level is
            # exempt because the prover never evaluates it at all.
            raise UnsupportedNode(
                "tuple unpacking is not in the proved subset; the prover's "
                "assignment handler accepts only names and subscripts",
            )
        if not isinstance(value, (list, tuple)):
            raise UnsupportedNode(f"cannot unpack {type_name(value)}")
        _unpack(target.elts, list(value), frame)
        return
    raise UnsupportedNode(f"cannot assign to {type(target).__name__} in the subset")


@eval_stmt.register(astroid.Return)
def _stmt_return(node: astroid.Return, frame: Frame) -> None:
    raise _Return(None if node.value is None else eval_expr(node.value, frame))


@eval_stmt.register(astroid.If)
def _stmt_if(node: astroid.If, frame: Frame) -> None:
    frame.step()
    branch = node.body if _truth(eval_expr(node.test, frame)) else node.orelse
    for stmt in branch or ():
        eval_stmt(stmt, frame)


@eval_stmt.register(astroid.For)
def _stmt_for(node: astroid.For, frame: Frame) -> None:
    if node.orelse:
        raise UnsupportedNode("for-else is not in the proved subset")
    if not isinstance(node.target, astroid.AssignName):
        # ``_eval_stmt.eval_for``: "tuple-unpack in for-loops is not supported
        # in v1; trusted layers should return Row-shaped rows".
        raise UnsupportedNode(
            "tuple unpacking in a for-loop target is not in the proved subset",
        )
    for statement in node.body:
        jumps = (astroid.Return, astroid.Break, astroid.Continue)
        for inner in (statement, *statement.nodes_of_class(jumps)):
            if isinstance(inner, jumps):
                # ``eval_for`` walks the body and refuses Break/Continue/Return.
                # Here a ``_Return`` would propagate out of the loop and be
                # absorbed by ``call``, running control flow the proof never
                # considered.
                # ``eval_for`` walks the body and refuses all three
                # *statically*, before evaluating anything. Refusing them only
                # when reached would let a branch that never executes change
                # whether a program runs at all.
                raise UnsupportedNode(
                    f"{type(inner).__name__.lower()} inside a for-loop is not "
                    "in the proved subset",
                )
    iterable = eval_expr(node.iter, frame)
    if not isinstance(iterable, (list, tuple, set, frozenset, str)):
        raise UnsupportedNode(
            f"cannot iterate over {type_name(iterable)} in the subset",
        )
    for item in list(iterable):
        frame.step()
        _assign_to(node.target, item, frame)
        for stmt in node.body:
            eval_stmt(stmt, frame)


@eval_stmt.register(astroid.Assert)
def _stmt_assert(node: astroid.Assert, frame: Frame) -> None:
    if _truth(eval_expr(node.test, frame)):
        return
    # ``eval_assert`` evaluates ``node.test`` only, so the failure message is
    # rendered from source rather than evaluated -- otherwise a failing assert
    # could fire an effect the proof never saw, on exactly the paths where the
    # prover and CPython already disagree (issues #110, #103).
    message = "assertion failed"
    if node.fail is not None:
        message = node.fail.as_string()
    raise ProgramError(message, kind="AssertionError")


@eval_stmt.register(astroid.Raise)
def _stmt_raise(node: astroid.Raise, frame: Frame) -> None:
    """``raise`` names an exception; the subset has no exception *values*.

    The prover records a raise as a reachability fact with the exception's
    name, so the interpreter reports the same thing rather than constructing a
    live Python exception object the program could then get hold of.
    """
    if node.exc is None:
        raise UnsupportedNode("bare 'raise' is not in the proved subset")
    exc = node.exc
    kind = exc.func.as_string() if isinstance(exc, astroid.Call) else exc.as_string()
    # Rendered from source, never evaluated. ``eval_raise`` collects exception
    # *names* via ``_get_all_bases`` and evaluates no operand, so evaluating one
    # here would perform work -- including a trusted call -- that the proof
    # never modelled.
    message = ""
    if isinstance(exc, astroid.Call) and exc.args:
        message = exc.args[0].as_string()
    raise ProgramError(message or kind, kind=kind)


# --------------------------------------------------------------------------
# Expressions
# --------------------------------------------------------------------------


@eval_expr.register(astroid.Const)
def _expr_const(node: astroid.Const, frame: Frame) -> Any:
    frame.step()
    value = node.value
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if value is Ellipsis:
        raise UnsupportedNode("Ellipsis is not in the subset")
    raise UnsupportedNode(
        f"constant of type {type(value).__name__} is not in the subset"
    )


@eval_expr.register(astroid.Name)
def _expr_name(node: astroid.Name, frame: Frame) -> Any:
    frame.step()
    return _lookup(node.name, frame)


def _lookup(name: str, frame: Frame) -> Any:
    """Resolve a name lexically.

    Note what is *not* here: no astroid inference, no ``sys.modules`` scan, no
    fallback to ``vars(builtins)``. A name resolves to a local, to a module-
    level binding the interpreter itself created, or to one of the builtins in
    :data:`clauz3.interp.builtins.BUILTINS` -- or it does not resolve.
    """
    own = frame.scope.get_own(name)
    if own is not _MISSING:
        return own
    if frame.unbound_local(name):
        # Statically local to this body or an enclosing one, and not bound yet.
        # CPython resolves the name at compile time, so neither the enclosing
        # binding nor the module global is a candidate. Reporting one instead
        # would be a silent wrong answer rather than a refusal.
        raise ProgramError(
            f"cannot access local variable {name!r} where it is not "
            "associated with a value",
            kind="UnboundLocalError",
        )
    value = frame.scope.get(name)
    if value is not _MISSING:
        return value
    value = frame.program.globals.get(name)
    if value is not _MISSING:
        return value
    builtin = BUILTINS.get(name)
    if builtin is not None:
        return builtin
    raise NameResolutionError(f"name {name!r} is not defined in the subset")


@eval_expr.register(astroid.Attribute)
def _expr_attribute(node: astroid.Attribute, frame: Frame) -> Any:
    """Attribute access over a closed table.

    The proved subset has no general attribute access: the solver's proxies
    expose a fixed ``methods`` table and ``Row`` exposes its declared fields.
    Matching that here is what makes ``x.__class__.__mro__``,
    ``gen.gi_frame.f_globals`` and ``"".format`` traversal non-issues -- not a
    denylist that has to name them, but the absence of any rule that would
    resolve them.
    """
    frame.step()
    value = eval_expr(node.expr, frame)
    if isinstance(value, TrustedModule):
        return value.attr(node.attrname)
    if isinstance(value, Row):
        if node.attrname in type(value).__annotations__:
            return getattr(value, node.attrname)
        raise UnsupportedNode(
            f"{type_name(value)} has no field {node.attrname!r}",
        )
    impl = subset_methods.lookup(value, node.attrname)
    if impl is not None:
        # A method the prover models with a real implementation. Refusing these
        # would make the interpreter narrower than the proved subset:
        # ``'sandbox/x'.startswith('sandbox')`` proves today.
        if node.attrname in subset_methods.PROPERTIES:
            return _require_in_domain(impl.__get__(value))
        return BoundMethod(receiver=value, name=node.attrname, impl=impl)
    if isinstance(value, RowClass):
        # Enumerated, like every other consumption point. A bare
        # ``getattr(value.schema, ...)`` here would resolve ``R.__mro__``,
        # ``R.__class__`` (the metaclass) and ``R.__subclasses__`` -- live
        # objects, and the one attribute path in the value domain with no
        # allowlist. Class-level field access yields a ``ColumnRef``, which
        # only ever appears inside ``@clauz3.guarantee(...)`` decorators, and
        # those are never evaluated -- so at runtime a Row *class* has no
        # readable attributes at all.
        raise UnsupportedNode(
            f"attribute access on the schema {value.name!r} is not in the "
            f"proved subset ({node.as_string()}); a Row class is a constructor "
            "and a schema, not a value with fields",
        )
    raise UnsupportedNode(
        f"attribute access on {type_name(value)} is not in the proved subset "
        f"({node.as_string()})",
    )


@eval_expr.register(astroid.Call)
def _expr_call(node: astroid.Call, frame: Frame) -> Any:
    func = eval_expr(node.func, frame)
    args = [eval_expr(arg, frame) for arg in node.args]
    kwargs: dict[str, Any] = {}
    for keyword in node.keywords or ():
        if keyword.arg is None:
            raise UnsupportedNode("dict unpacking in a call is not in the subset")
        kwargs[keyword.arg] = eval_expr(keyword.value, frame)
    return call(func, args=args, kwargs=kwargs, frame=frame)


@eval_expr.register(astroid.BinOp)
def _expr_binop(node: astroid.BinOp, frame: Frame) -> Any:
    frame.step()
    return _binop(node.op, eval_expr(node.left, frame), eval_expr(node.right, frame))


_BINOPS: dict[str, Callable[[Any, Any], Any]] = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a / b,
    "//": lambda a, b: a // b,
    "%": lambda a, b: a % b,
    "**": lambda a, b: a**b,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    "^": lambda a, b: a ^ b,
    "<<": lambda a, b: a << b,
    ">>": lambda a, b: a >> b,
}


def _binop(op: str, left: Any, right: Any) -> Any:
    impl = _BINOPS.get(op)
    if impl is None:
        raise UnsupportedNode(f"operator {op!r} is not in the subset")
    _require_operand(op, left)
    _require_operand(op, right)
    try:
        return _require_in_domain(impl(left, right))
    except (TypeError, ArithmeticError) as exc:
        # ArithmeticError covers ZeroDivisionError and OverflowError alike:
        # ``10 ** 400 * 1.0`` overflows without dividing by zero.
        raise ProgramError(str(exc), kind=type(exc).__name__) from exc


def _require_operand(op: str, value: Any) -> None:
    """Operands are always subset data, so CPython's operators are safe here.

    There are no user-defined classes in the subset -- the only nominal type is
    ``Row``, whose only operator dunders are ``__eq__``/``__hash__`` -- so
    ``a + b`` cannot dispatch into attacker-written code the way it could if
    the program could define ``__add__``.
    """
    if value is None or isinstance(
        value, (bool, int, float, str, list, tuple, set, frozenset, dict, Row)
    ):
        return
    raise UnsupportedNode(f"operator {op!r} does not apply to {type_name(value)}")


@eval_expr.register(astroid.UnaryOp)
def _expr_unaryop(node: astroid.UnaryOp, frame: Frame) -> Any:
    frame.step()
    value = eval_expr(node.operand, frame)
    if node.op == "not":
        return not _truth(value)
    _require_operand(node.op, value)
    impl = _UNARYOPS.get(node.op)
    if impl is None:
        raise UnsupportedNode(f"unary {node.op!r} is not in the subset")
    try:
        return _require_in_domain(impl(value))
    except (TypeError, ArithmeticError) as exc:
        # ``-"a"``, ``~1.5``, ``-[1]``: the operand passed the domain check but
        # the operator does not apply. That is a program fault, not an
        # interpreter fault, and it must not escape as a native exception.
        raise ProgramError(str(exc), kind=type(exc).__name__) from exc


_UNARYOPS: dict[str, Callable[[Any], Any]] = {
    "-": lambda a: -a,
    "+": lambda a: +a,
    "~": lambda a: ~a,
}


def _require_in_domain(value: Any) -> Any:
    """Admit only values inside the domain -- an allowlist, not a denylist.

    ``(-8) ** 0.5`` is a ``complex``, a type ``values.py`` states can never be
    held, and CPython produces it happily. An earlier version named ``complex``
    specifically; enumerating what is *in* means a modelled method returning
    something unforeseen is refused rather than admitted, which is the same
    inversion this design applies everywhere else.
    """
    if value is None or isinstance(
        value, (bool, int, float, str, list, tuple, set, frozenset, dict, Row)
    ):
        return value
    raise UnsupportedNode(
        f"{type_name(value)} is not in the subset value domain",
    )


_COMPARISONS: dict[str, Callable[[Any, Any], Any]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "in": lambda a, b: a in b,
    "not in": lambda a, b: a not in b,
}


@eval_expr.register(astroid.Compare)
def _expr_compare(node: astroid.Compare, frame: Frame) -> Any:
    """Chained comparison with full Python semantics.

    ``0 < x < 10`` means both comparisons, with each intermediate evaluated
    once. ``deal_solver.eval_compare`` says the same thing symbolically -- it
    conjoins every pair and reuses the previously-evaluated right as the next
    left. An earlier version of this docstring claimed the prover enforced
    only the first comparator; that was true of the pre-#43 prover, not this
    one, and ``tests/test_chained_comparisons.py`` pins the fix.
    """
    frame.step()
    left = eval_expr(node.left, frame)
    for op, right_node in node.ops:
        impl = _COMPARISONS.get(op)
        if impl is None:
            raise UnsupportedNode(f"comparison {op!r} is not in the subset")
        right = eval_expr(right_node, frame)
        _require_operand(op, left)
        _require_operand(op, right)
        try:
            if not impl(left, right):
                return False
        except TypeError as exc:
            raise ProgramError(str(exc), kind="TypeError") from exc
        left = right
    return True


_BOOLOPS: frozenset[str] = frozenset({"and", "or"})


@eval_expr.register(astroid.BoolOp)
def _expr_boolop(node: astroid.BoolOp, frame: Frame) -> Any:
    """Short-circuiting ``and``/``or``, returning the deciding operand.

    Named as a table so ``test_operator_tables_are_a_subset_of_the_prover`` can
    compare it against ``BOOL_OPERATIONS``. An earlier version compared
    ``node.op`` inline and fell through to "return the last value" for anything
    else -- unreachable given astroid's grammar, but open by default rather
    than closed, which is the wrong direction for this file.
    """
    if node.op not in _BOOLOPS:
        raise UnsupportedNode(f"boolean operator {node.op!r} is not in the subset")
    frame.step()
    result: Any = None
    for value_node in node.values:
        result = eval_expr(value_node, frame)
        if node.op == "and" and not _truth(result):
            return result
        if node.op == "or" and _truth(result):
            return result
    return result


@eval_expr.register(astroid.IfExp)
def _expr_ifexp(node: astroid.IfExp, frame: Frame) -> Any:
    frame.step()
    if _truth(eval_expr(node.test, frame)):
        return eval_expr(node.body, frame)
    return eval_expr(node.orelse, frame)


@eval_expr.register(astroid.List)
def _expr_list(node: astroid.List, frame: Frame) -> list[Any]:
    frame.step()
    return [eval_expr(item, frame) for item in node.elts]


@eval_expr.register(astroid.Tuple)
def _expr_tuple(node: astroid.Tuple, frame: Frame) -> tuple[Any, ...]:
    frame.step()
    return tuple(eval_expr(item, frame) for item in node.elts)


@eval_expr.register(astroid.Set)
def _expr_set(node: astroid.Set, frame: Frame) -> set[Any]:
    frame.step()
    try:
        return {eval_expr(item, frame) for item in node.elts}
    except TypeError as exc:
        # ``{[1]}`` -- an unhashable element. A program fault, not an
        # interpreter fault, and it must not escape as a native TypeError.
        raise ProgramError(str(exc), kind="TypeError") from exc


@eval_expr.register(astroid.Dict)
def _expr_dict(node: astroid.Dict, frame: Frame) -> dict[Any, Any]:
    frame.step()
    try:
        return {
            eval_expr(key, frame): eval_expr(value, frame) for key, value in node.items
        }
    except TypeError as exc:
        # ``{[1]: 2}`` -- an unhashable key. A program fault, not an interpreter
        # fault, and it must not escape as a native TypeError.
        raise ProgramError(str(exc), kind="TypeError") from exc


@eval_expr.register(astroid.Subscript)
def _expr_subscript(node: astroid.Subscript, frame: Frame) -> Any:
    frame.step()
    value = eval_expr(node.value, frame)
    if not isinstance(value, (list, tuple, str, dict)):
        raise UnsupportedNode(f"cannot subscript {type_name(value)} in the subset")
    if isinstance(node.slice, astroid.Slice):
        if node.slice.step is not None:
            raise UnsupportedNode("slice step is not in the subset")
        if isinstance(value, dict):
            raise UnsupportedNode("cannot slice a dict")
        lower = None if node.slice.lower is None else eval_expr(node.slice.lower, frame)
        upper = None if node.slice.upper is None else eval_expr(node.slice.upper, frame)
        try:
            return value[lower:upper]
        except (TypeError, ValueError) as exc:
            raise ProgramError(str(exc), kind=type(exc).__name__) from exc
    key = eval_expr(node.slice, frame)
    try:
        return value[key]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProgramError(str(exc), kind=type(exc).__name__) from exc


@eval_expr.register(astroid.ListComp)
def _expr_listcomp(node: astroid.ListComp, frame: Frame) -> list[Any]:
    frame.step()
    return list(_comprehend(node, frame))


def _comprehend(node: astroid.ListComp, frame: Frame) -> Iterable[Any]:
    if len(node.generators) != 1:
        raise UnsupportedNode("only single-generator comprehensions are in the subset")
    generator = node.generators[0]
    if generator.is_async:
        raise UnsupportedNode("async comprehensions are not in the subset")
    iterable = eval_expr(generator.iter, frame)
    if not isinstance(iterable, (list, tuple, set, frozenset, str)):
        raise UnsupportedNode(f"cannot iterate over {type_name(iterable)}")
    for item in list(iterable):
        frame.step()
        inner = Frame(
            program=frame.program,
            scope=frame.scope.child(),
            trace=frame.trace,
            module_scope=frame.module_scope,
            enclosing=frame.nested(),
        )
        _assign_to(generator.target, item, inner)
        if all(_truth(eval_expr(cond, inner)) for cond in generator.ifs or ()):
            yield eval_expr(node.elt, inner)


@eval_expr.register(astroid.Lambda)
def _expr_lambda(node: astroid.Lambda, frame: Frame) -> LambdaFunction:
    frame.step()
    # ``node.args`` goes through the same gate as a ``def``'s. Omitting it was
    # not a narrower subset but a wider one: ``lambda x=send_email(...): x``
    # bound a ``LambdaFunction`` whose defaults were never evaluated *and*
    # never refused, so the effect vanished -- and a literal default that
    # ``_eval_defaults`` would have admitted failed with "missing argument".
    return LambdaFunction(
        node=node,
        closure=frame.scope,
        enclosing=frame.nested(),
        defaults=_eval_defaults(node.args, frame),
    )


def _truth(value: Any) -> bool:
    """Truthiness over subset values only.

    ``bool(x)`` on a live Python object can run ``__bool__``; here every value
    is subset data, so this is CPython's truthiness with no dispatch surface.
    """
    if value is None or isinstance(
        value, (bool, int, float, str, list, tuple, set, frozenset, dict)
    ):
        return bool(value)
    if isinstance(value, Row):
        return True
    raise UnsupportedNode(f"{type_name(value)} has no truth value in the subset")
