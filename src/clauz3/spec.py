"""Declarative contract helpers over trusted effect facts."""

from __future__ import annotations

import ast
import inspect
import itertools
from collections.abc import Callable, Container, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast, overload

import z3

if TYPE_CHECKING:
    from types import CodeType

    from clauz3.row import ColumnRef

from clauz3._vendor.deal_solver._bool_op import eval_boolean_operands
from clauz3._vendor.deal_solver._context._layer import Layer
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._funcs._registry import register
from clauz3._vendor.deal_solver._proxies import (
    BoolSort,
    DictSort,
    ProxySort,
    UntypedDictSort,
    UntypedListSort,
    UntypedSetSort,
    UntypedVarTupleSort,
    and_expr,
    if_expr,
    or_expr,
    types,
)
from clauz3.contract_literals import ContractLiteralArgument

F = TypeVar("F", bound=Callable[..., object])

# String methods allowed inside relation lambdas. These map onto the matching
# proxy methods (PrefixOf / SuffixOf), letting contracts express path-prefix
# policies such as "every write path is under this root".
_STR_METHODS = frozenset({"startswith", "endswith"})

# Unique suffixes for the fresh bound variables the distinct clauses introduce.
_FRESH_COUNTER = itertools.count()


#: Fully-qualified names of every helper decorated with :func:`contract`.
#:
#: ``deal_solver``'s ``FUNCTIONS`` registry also holds builtins, ``math``,
#: ``os.path`` and friends, so it cannot answer "is this name a domain
#: contract?". This set can, and it is what
#: :mod:`clauz3.guarantees` checks a guarantee's callee against.
REGISTERED_CONTRACTS: set[str] = set()


@overload
def contract(func: F, *, description: str | None = None) -> F: ...


@overload
def contract(*, description: str) -> Callable[[F], F]: ...


def contract(
    func: F | None = None, *, description: str | None = None
) -> F | Callable[[F], F]:
    """Register a domain contract helper.

    The helper itself builds a declarative ContractSpec. The registered wrapper
    is the bridge deal-solver calls when it sees ``module.helper(...)`` in a
    guarantee. An optional trusted description uses named parameter placeholders
    for deterministic approval text, e.g. ``description="At most {count} calls."``.
    """

    if func is None:
        if description is None:
            raise TypeError("contract requires a function or description")
        return lambda helper: contract(helper, description=description)
    if description is not None:
        from clauz3.contract_metadata import (
            DESCRIPTION_ATTRIBUTE,
            parameter_types,
            validate_template,
        )

        validate_template(description, parameter_types(inspect.signature(func)))
        setattr(func, DESCRIPTION_ATTRIBUTE, description)
    qualified_name = f"{func.__module__}.{func.__name__}"
    register(qualified_name, owner=func, is_contract=True, signature=func)(
        _solve_with(func)
    )
    REGISTERED_CONTRACTS.add(qualified_name)
    return func


class RelationError(UnsupportedError):
    """A relation names an effect, or a field, that does not exist.

    Raised where the mistake is --- building the relation --- rather than left
    to matter at solve time. Matching nothing is not a safe failure: a contract
    over an empty relation is a conjunction of no clauses, which is ``True``,
    so an unresolvable name would make its guarantees hold vacuously.

    An :class:`UnsupportedError` so that a relation built inside a contract
    helper, during solving, ends the proof as a refusal rather than escaping as
    a traceback.
    """


#: Signatures of the trusted effects the proof in progress may address, as
#: ``{effect name: parameter names}``, or ``None`` outside a proof (a contract
#: module imported on its own has nothing to check against).
_EFFECT_SIGNATURES: dict[str, tuple[str, ...]] | None = None

#: The trusted layer's own files, whose relations are checked as they are
#: built. See :func:`known_effects`.
_CHECKED_FILES: Container[str] = ()

#: The relations each source file has built, so the ones a cached contract
#: module built during an earlier proof can be re-checked against this proof's
#: layer. Import caching means :func:`effect` runs once per process, not once
#: per proof --- but a relation built *inside* a contract helper is rebuilt on
#: every call, so these are sets of an immutable value rather than a list: two
#: proofs of the same helper add one entry, not one each. Grouped by file
#: because that is how :func:`validate_relations` looks them up.
_RELATIONS: dict[str, set[EffectRelation]] = {}


@contextmanager
def known_effects(
    signatures: Mapping[str, tuple[str, ...]],
    *,
    files: Container[str] = (),
) -> Iterator[None]:
    """Check the relations of the layer being proved against ``signatures``.

    ``files`` are the trusted layer's own source files. A relation built there
    is checked the moment it is built, which is where the mistake is and what
    gives a configuration error rather than a proof result.

    A relation built anywhere else is not checked here, because a contract
    module may legitimately import one from a layer that is not part of *this*
    proof: a layer can import another layer's contracts to reuse a rule, and
    proving a case against the first layer does not put the second layer's
    effects in scope.
    Such a relation is checked when it is used instead --- see
    :meth:`EffectRelation.facts`.

    The window is a module global. Public operations enter the shared trusted
    layer coordinator before this context, serializing selection and restoring
    the previous signatures for nested uses.
    """

    global _EFFECT_SIGNATURES, _CHECKED_FILES
    previous, previous_files = _EFFECT_SIGNATURES, _CHECKED_FILES
    _EFFECT_SIGNATURES, _CHECKED_FILES = dict(signatures), files
    try:
        yield
    finally:
        _EFFECT_SIGNATURES, _CHECKED_FILES = previous, previous_files


def validate_relations(
    signatures: Mapping[str, tuple[str, ...]],
    *,
    files: Iterable[str],
) -> None:
    """Re-check the relations built by ``files``, whenever they were built.

    A contract module is imported once per process, so a second proof in the
    same process (pytest, ``clauz3 test``, the approval service) never re-runs
    its module-level :func:`effect` calls, and the window in
    :func:`known_effects` would miss them.

    Scoping is by source *file* rather than module name because two layers can
    share a name: this repository has ``tools.gh.trusted.contracts`` under both
    ``stdlib/gh`` and ``examples/gh-triage``. Python caches the first one
    imported, so checking by name would test one layer's relations against the
    other layer's effects.
    """

    for origin in files:
        for relation in _RELATIONS.get(origin, frozenset()):
            relation.check_names(signatures)


def effect(*targets: str | Callable[..., object]) -> EffectRelation:
    """Create a relation over the calls to one or more trusted functions.

    Name the function itself where the contract can see it --- the import is
    then checked by Python, mypy and your editor's rename::

        from tools.email.trusted.effects import send_email

        Email = effect(send_email)

    A string names the same thing for a layer that cannot import its effects,
    and is checked against the trusted layer when the proof loads it. Several
    targets make one relation over all of them, ``effect(edit_file,
    append_file)``, and no target at all is the relation over *every* trusted
    call a program makes, which is how a contract says "this program has no
    effects".

    Row fields come from the parameters: ``send_email(addr, msg)`` gives
    ``e.addr`` and ``e.msg``, and a relation over several effects offers the
    fields they all share, since a predicate must hold of every fact it
    matches.

    A relation could once name a ``@deal.has`` marker instead, so
    ``effect("read")`` matched whichever functions happened to carry the string
    ``read``: a set the contract could not see, that a later effect (or an
    unrelated layer installed alongside) could silently join or leave. Grouping
    lives in the contract that depends on it now, and is checked.
    """

    names = tuple(
        target if isinstance(target, str) else target.__name__ for target in targets
    )
    relation = EffectRelation(
        names=names,
        fields=_fields_for(names, targets),
        origin=_defining_file(),
    )
    if _EFFECT_SIGNATURES is not None and relation.origin in _CHECKED_FILES:
        relation.check_names(_EFFECT_SIGNATURES)
    _RELATIONS.setdefault(relation.origin, set()).add(relation)
    return relation


def _fields_for(
    names: tuple[str, ...],
    targets: tuple[str | Callable[..., object], ...],
) -> frozenset[str] | None:
    """The fields a predicate over this relation may read, or ``None``.

    ``None`` means "not known here": a relation named by strings, built outside
    a proof, has no signature to read. The fields of a relation over several
    effects are the ones they have in common, because a predicate is applied to
    every matching fact.
    """

    if not names:
        return None  # every effect; a predicate cannot assume a shared field
    per_effect: list[frozenset[str]] = []
    for name, target in zip(names, targets, strict=True):
        if _EFFECT_SIGNATURES is not None and name in _EFFECT_SIGNATURES:
            per_effect.append(frozenset(_EFFECT_SIGNATURES[name]))
        elif callable(target):
            per_effect.append(frozenset(inspect.signature(target).parameters))
        else:
            return None
    return frozenset.intersection(*per_effect)


def _defining_file() -> str:
    """The source file that called :func:`effect`, for scoping re-validation."""

    frame = inspect.currentframe()
    caller = frame.f_back.f_back if frame and frame.f_back else None
    if caller is None:  # pragma: no cover - CPython always gives us a frame
        return ""
    path = caller.f_globals.get("__file__")
    return str(Path(path).resolve()) if path else ""


def no_guarantees() -> ContractSpec:
    """Return a contract that imposes no constraints."""
    return NoGuaranteesSpec()


def _solve_with(func: F) -> Callable[..., BoolSort]:
    return _ContractSolver(func)


class _ContractSolver:
    """An exact marker for calls whose arguments are Python source literals."""

    def __init__(self, func: Callable[..., object]) -> None:
        self.func = func

    def __call__(self, *args: object, ctx: Any, **kwargs: object) -> BoolSort:
        func = self.func
        unwrapped_args = [_unwrap_proxy_arg(a) for a in args]
        unwrapped_kwargs = {k: _unwrap_proxy_arg(v) for k, v in kwargs.items()}
        try:
            result = func(*unwrapped_args, **unwrapped_kwargs)
        except UnsupportedError:
            raise
        except Exception as exc:
            # A trusted helper body is ordinary Python; a bad argument (an
            # unknown column name, a type mismatch) raises an arbitrary
            # exception. Surface it as a skipped proof rather than a traceback
            # out of prove_text (#133).
            raise UnsupportedError(
                f"contract helper {func.__module__}.{func.__qualname__} raised "
                f"{type(exc).__name__}: {exc} (a trusted-layer bug or a bad "
                "guarantee argument, not an unsupported construct)"
            ) from exc
        if not isinstance(result, ContractSpec):
            raise UnsupportedError("contract helper must return ContractSpec")
        try:
            return result.solve(ctx=ctx)
        except (UnsupportedError, z3.Z3Exception):
            raise
        except Exception as exc:
            # Encoding faults (row fields, lambda compilation, substitution)
            # arise here rather than in the helper body; contain them the
            # same way so no proof ends in a traceback (#133).
            raise UnsupportedError(
                f"contract helper {func.__module__}.{func.__qualname__} could "
                f"not be encoded: {type(exc).__name__}: {exc}"
            ) from exc


def _unwrap_proxy_arg(value: Any) -> Any:
    """Never expose a symbolic object's Python truthiness to a builder."""
    from clauz3._vendor.deal_solver._proxies._row import RowClassSort

    if isinstance(value, ContractLiteralArgument):
        return value.value
    if isinstance(value, RowClassSort):
        return value.schema
    raise UnsupportedError(
        "contract helper arguments must be source literals or Row classes"
    )


class ContractSpec:
    def solve(self, *, ctx: Any) -> BoolSort:
        raise NotImplementedError


@dataclass(frozen=True)
class NoGuaranteesSpec(ContractSpec):
    def solve(self, *, ctx: Any) -> BoolSort:
        return types.bool.val(True, ctx=ctx)


class Relation:
    """A bag of facts that contracts query: trusted calls, or events they perform.

    Subclasses say which facts they cover (:meth:`facts`) and which fields a
    predicate over them may read (:attr:`fields`); the query methods are shared.
    """

    #: The fields a predicate may read, or ``None`` when they are not known.
    fields: frozenset[str] | None

    def describe(self) -> str:
        """How error messages name this relation."""
        raise NotImplementedError

    def facts(self, ctx: Any) -> list[Any]:
        raise NotImplementedError

    def _checked(self, predicate: Callable[[Any], Any]) -> LambdaSpec:
        """Compile a predicate, refusing a field the matched facts cannot have."""

        spec = LambdaSpec.from_callable(predicate)
        if self.fields is None:
            return spec
        unknown = sorted(spec.fields() - self.fields)
        if unknown:
            raise RelationError(
                f"predicate over {self.describe()} reads "
                f"{', '.join(repr(field) for field in unknown)}, which is not "
                f"a field of every fact it covers. Available fields: "
                f"{sorted(self.fields)!r}."
            )
        return spec

    def all(self, predicate: Callable[[Any], Any]) -> ContractSpec:
        return AllSpec(relation=self, predicate=self._checked(predicate))

    def where(self, predicate: Callable[[Any], Any]) -> FilteredRelation:
        return FilteredRelation(relation=self, predicate=self._checked(predicate))

    def empty(self) -> ContractSpec:
        return EmptySpec(relation=self)

    def distinct(self, key: Callable[[Any], Any]) -> ContractSpec:
        return DistinctSpec(relation=self, key=self._checked(key))

    def sum(self, selector: Callable[[Any], Any]) -> SumSpec:
        return SumSpec(
            relation=self,
            selector=self._checked(selector),
            predicate=None,
        )

    def count(self) -> SumSpec:
        return SumSpec(relation=self, selector=None, predicate=None)


@dataclass(frozen=True)
class EffectRelation(Relation):
    #: The trusted functions this relation covers; empty means every one.
    names: tuple[str, ...]
    #: The fields a predicate may read, or ``None`` when they are not known
    #: here (see :func:`_fields_for`).
    fields: frozenset[str] | None = None
    #: The source file that built this relation, for scoped re-validation.
    origin: str = ""

    def __post_init__(self) -> None:
        # ``EffectRelation("send_email")`` would iterate the string, so the
        # relation would cover eleven one-letter effects and match nothing.
        # :func:`effect` never does this, but the class is reachable from a
        # trusted layer.
        if isinstance(self.names, str):
            raise RelationError(
                f"effect names must be a tuple, not the string {self.names!r}; "
                "use effect(...) to build a relation.",
            )

    def describe(self) -> str:
        return f"effect({', '.join(self.names)})"

    def check_names(self, signatures: Mapping[str, tuple[str, ...]]) -> None:
        """Refuse a name that is not a trusted effect of the layer being proved."""

        unknown = [name for name in self.names if name not in signatures]
        if not unknown:
            return
        raise RelationError(
            f"effect({', '.join(repr(name) for name in self.names)}) in "
            f"{self.origin or 'a contract module'} names "
            f"{', '.join(repr(name) for name in unknown)}, which is not a "
            f"trusted effect. Known effects: {sorted(signatures)!r}. "
            "(A name that matches nothing would make every contract over this "
            "relation hold vacuously. If it was a @deal.has marker, name the "
            "function that carries it.)"
        )

    def facts(self, ctx: Any) -> list[Any]:
        # Checked on use, not only where it was built. A relation imported from
        # a layer outside this proof's trusted roots passes through neither the
        # window in ``known_effects`` nor ``validate_relations``, and neither
        # does one built inside a contract helper while solving. Matching
        # nothing is not a safe answer: it is what makes a contract hold
        # vacuously, so a name that resolves to no effect is refused here.
        if _EFFECT_SIGNATURES is not None:
            self.check_names(_EFFECT_SIGNATURES)
        return [fact for fact in ctx.facts if not self.names or fact.name in self.names]


@dataclass(frozen=True)
class FilteredRelation:
    relation: Relation
    predicate: LambdaSpec

    def all(self, predicate: Callable[[Any], Any]) -> ContractSpec:
        return AllSpec(
            relation=self.relation,
            predicate=self.relation._checked(predicate),
            fact_filter=self.predicate,
        )

    def empty(self) -> ContractSpec:
        return EmptySpec(relation=self.relation, fact_filter=self.predicate)

    def distinct(self, key: Callable[[Any], Any]) -> ContractSpec:
        return DistinctSpec(
            relation=self.relation,
            key=self.relation._checked(key),
            fact_filter=self.predicate,
        )

    def sum(self, selector: Callable[[Any], Any]) -> SumSpec:
        return SumSpec(
            relation=self.relation,
            selector=self.relation._checked(selector),
            predicate=self.predicate,
        )

    def count(self) -> SumSpec:
        return SumSpec(relation=self.relation, selector=None, predicate=self.predicate)

    def shares_value(
        self,
        other: FilteredRelation,
        key: Callable[[Any], Any],
    ) -> ContractSpec:
        # The key is read off facts from *both* sides, so it must name a field
        # each of them has.
        self.relation._checked(key)
        return SharedValueSpec(
            left_relation=self.relation,
            left_filter=self.predicate,
            right_relation=other.relation,
            right_filter=other.predicate,
            key=other.relation._checked(key),
        )


@dataclass(frozen=True)
class AllSpec(ContractSpec):
    relation: Relation
    predicate: LambdaSpec
    fact_filter: LambdaSpec | None = None

    def solve(self, *, ctx: Any) -> BoolSort:
        clauses = []
        for fact in self.relation.facts(ctx):
            cond = _fact_cond(fact=fact, predicate=self.fact_filter, ctx=ctx)
            body_pred = or_expr(
                cond.m_not(ctx=ctx),
                self.predicate.evaluate(row=fact.args, ctx=ctx).m_bool(ctx=ctx),
                ctx=ctx,
            )
            wrapped = _wrap_with_quantifiers(fact, body_pred, ctx=ctx)
            clauses.append(wrapped)
        return and_expr(*clauses, ctx=ctx)


@dataclass(frozen=True)
class EmptySpec(ContractSpec):
    relation: Relation
    fact_filter: LambdaSpec | None = None

    def solve(self, *, ctx: Any) -> BoolSort:
        clauses = []
        for fact in self.relation.facts(ctx):
            cond = _fact_cond(fact=fact, predicate=self.fact_filter, ctx=ctx)
            body_pred = cond.m_not(ctx=ctx)
            wrapped = _wrap_with_quantifiers(fact, body_pred, ctx=ctx)
            clauses.append(wrapped)
        return and_expr(*clauses, ctx=ctx)


@dataclass(frozen=True)
class DistinctSpec(ContractSpec):
    relation: Relation
    key: LambdaSpec
    fact_filter: LambdaSpec | None = None

    def solve(self, *, ctx: Any) -> BoolSort:
        clauses = []
        facts = self.relation.facts(ctx)
        for index, left in enumerate(facts):
            left_cond = _fact_cond(fact=left, predicate=self.fact_filter, ctx=ctx)
            # Cross-fact pairs (existing behavior, now quantifier-aware)
            for right in facts[index + 1 :]:
                right_cond = _fact_cond(
                    fact=right,
                    predicate=self.fact_filter,
                    ctx=ctx,
                )
                clauses.append(
                    _distinct_pair_clause(
                        left=left,
                        right=right,
                        left_cond=left_cond,
                        right_cond=right_cond,
                        key=self.key,
                        ctx=ctx,
                    )
                )
            # Same-fact case: only meaningful when fact has quantifiers
            if getattr(left, "quantifiers", ()):
                clauses.append(
                    _distinct_same_fact_clause(
                        fact=left,
                        cond=left_cond,
                        key=self.key,
                        ctx=ctx,
                    )
                )
        return and_expr(*clauses, ctx=ctx)


def _distinct_pair_clause(
    *,
    left: Any,
    right: Any,
    left_cond: Any,
    right_cond: Any,
    key: LambdaSpec,
    ctx: Any,
) -> BoolSort:
    """Cross-fact: ∀ qvars_left, qvars_right: bounds ∧ conds → key(left) ≠ key(right).

    Each side's bound variables are renamed to fresh copies before quantifying.
    Two facts emitted by the same loop body share the same index symbol; without
    renaming, the clause would only compare the two call sites on the *same*
    iteration and never ``left(i)`` against ``right(j)`` for ``i ≠ j`` (#126).
    """
    left_quantifiers = list(getattr(left, "quantifiers", ()))
    right_quantifiers = list(getattr(right, "quantifiers", ()))
    if not left_quantifiers and not right_quantifiers:
        left_key = key.evaluate(row=left.args, ctx=ctx)
        right_key = key.evaluate(row=right.args, ctx=ctx)
        return or_expr(
            left_cond.m_not(ctx=ctx),
            right_cond.m_not(ctx=ctx),
            left_key.m_ne(right_key, ctx=ctx),
            ctx=ctx,
        )

    uid = next(_FRESH_COUNTER)
    fresh_left = [z3.Int(f"{q.bound_var}_lhs_{uid}") for q in left_quantifiers]
    fresh_right = [z3.Int(f"{q.bound_var}_rhs_{uid}") for q in right_quantifiers]
    subs_left = list(
        zip([q.bound_var for q in left_quantifiers], fresh_left, strict=True)
    )
    subs_right = list(
        zip([q.bound_var for q in right_quantifiers], fresh_right, strict=True)
    )

    # Only a quantified side needs renaming. Routing an unquantified fact
    # through _substituted_proxy would reject arg proxies it cannot rewrap,
    # refusing mixed pairs (one looped, one straight-line) for no reason.
    args_left = _substituted_args(left.args, subs_left)
    args_right = _substituted_args(right.args, subs_right)
    cond_left = _substituted_proxy(left_cond, subs_left) if subs_left else left_cond
    cond_right = (
        _substituted_proxy(right_cond, subs_right) if subs_right else right_cond
    )
    left_key = key.evaluate(row=args_left, ctx=ctx)
    right_key = key.evaluate(row=args_right, ctx=ctx)

    # Bounds may mention enclosing indices too (e.g. an inner result length).
    # Rename the whole bounds expression, not just this frame's index.
    bounds = z3.And(
        *[z3.substitute(q.bounds_expr(), subs_left) for q in left_quantifiers],
        *[z3.substitute(q.bounds_expr(), subs_right) for q in right_quantifiers],
    )
    key_diff = left_key.m_ne(right_key, ctx=ctx)
    body = z3.Implies(
        z3.And(bounds, _z3_expr(cond_left), _z3_expr(cond_right)),
        _z3_expr(key_diff),
    )
    wrapped = z3.ForAll(fresh_left + fresh_right, body)
    return types.bool(expr=wrapped)


def _substituted_args(
    args: dict[str, Any], subs: list[tuple[Any, Any]]
) -> dict[str, Any]:
    if not subs:
        return dict(args)
    return {k: _substituted_proxy(v, subs) for k, v in args.items()}


def _z3_expr(value: Any) -> Any:
    return value.expr if hasattr(value, "expr") else value


def _distinct_same_fact_clause(
    *, fact: Any, cond: Any, key: LambdaSpec, ctx: Any
) -> BoolSort:
    """Same-fact: ∀ i≠j, bounds(i) ∧ bounds(j) ∧ cond(i) ∧ cond(j) → key(i) ≠ key(j).

    Substitutes the bound vars with fresh copies per side of the comparison.
    """
    fresh_left = [z3.Int(f"{q.bound_var}_lhs_{id(fact)}") for q in fact.quantifiers]
    fresh_right = [z3.Int(f"{q.bound_var}_rhs_{id(fact)}") for q in fact.quantifiers]

    subs_left = list(
        zip([q.bound_var for q in fact.quantifiers], fresh_left, strict=True)
    )
    subs_right = list(
        zip([q.bound_var for q in fact.quantifiers], fresh_right, strict=True)
    )

    args_left = {k: _substituted_proxy(v, subs_left) for k, v in fact.args.items()}
    args_right = {k: _substituted_proxy(v, subs_right) for k, v in fact.args.items()}
    cond_left = _substituted_proxy(cond, subs_left)
    cond_right = _substituted_proxy(cond, subs_right)

    key_left = key.evaluate(row=args_left, ctx=ctx)
    key_right = key.evaluate(row=args_right, ctx=ctx)

    different_iteration = z3.Or(
        *[lv != rv for lv, rv in zip(fresh_left, fresh_right, strict=True)]
    )
    bounds = z3.And(
        *[z3.substitute(q.bounds_expr(), subs_left) for q in fact.quantifiers],
        *[z3.substitute(q.bounds_expr(), subs_right) for q in fact.quantifiers],
    )
    cond_left_z3 = cond_left.expr if hasattr(cond_left, "expr") else cond_left
    cond_right_z3 = cond_right.expr if hasattr(cond_right, "expr") else cond_right
    key_diff = key_left.m_ne(key_right, ctx=ctx)
    key_diff_z3 = key_diff.expr if hasattr(key_diff, "expr") else key_diff

    body = z3.Implies(
        z3.And(different_iteration, bounds, cond_left_z3, cond_right_z3),
        key_diff_z3,
    )
    wrapped = z3.ForAll(fresh_left + fresh_right, body)
    return types.bool(expr=wrapped)


def _substituted_proxy(value: Any, subs: list[tuple[Any, Any]]) -> Any:
    """Apply Z3 substitute() to the underlying expression, rewrapping in the same type.

    Returns non-proxy values (Python literals) unchanged. Special-cases proxy
    types that carry metadata beyond the canonical ``expr`` attribute so the
    metadata is preserved through substitution. Falling through to the default
    ``proxy_type(expr=new_expr)`` path for unknown metadata-carrying proxies
    would silently drop their schema/subtypes, so we raise a pointing
    UnsupportedError instead.
    """
    from clauz3._vendor.deal_solver._proxies._row import RowSort

    if not hasattr(value, "expr"):
        return value
    new_expr = z3.substitute(value.expr, subs)
    if new_expr.eq(value.expr):
        # Nothing to rename: the value does not mention any bound variable.
        # Returning it untouched sidesteps rebuilding proxies that cannot be
        # rebuilt at all -- the untyped empty containers (``[]``, ``()``,
        # ``{}``, ``set()``) raise from ``subtypes``/``evolve`` -- and any
        # loop-invariant argument.
        return value

    if isinstance(value, RowSort):
        return RowSort(schema=value.schema, expr=new_expr)

    proxy_type = type(value)
    try:
        # Container proxies (list, set, dict, tuple) carry element subtypes and
        # assert on them in their constructors; ``evolve`` rebuilds the proxy
        # with that metadata intact. Scalars have no evolve and take the plain
        # constructor.
        evolve = getattr(value, "evolve", None)
        if evolve is not None:
            return evolve(expr=new_expr)
        return proxy_type(expr=new_expr)
    except (TypeError, AssertionError, NotImplementedError, AttributeError) as exc:
        raise UnsupportedError(
            f"distinct over a quantified fact with arg type "
            f"{proxy_type.__name__!r} is not supported in v1 — "
            f"the proxy carries metadata that _substituted_proxy "
            f"cannot reconstruct ({exc}). See issue #13."
        ) from exc


@dataclass(frozen=True)
class SumSpec:
    relation: Relation
    selector: LambdaSpec | None
    predicate: LambdaSpec | None

    def __le__(self, other: Any) -> ContractSpec:
        return ComparisonSpec(left=self, op="<=", right=other)

    def __lt__(self, other: Any) -> ContractSpec:
        return ComparisonSpec(left=self, op="<", right=other)

    def __ge__(self, other: Any) -> ContractSpec:
        return ComparisonSpec(left=self, op=">=", right=other)

    def __gt__(self, other: Any) -> ContractSpec:
        return ComparisonSpec(left=self, op=">", right=other)

    def value(self, *, ctx: Any) -> ProxySort:
        import z3

        total: ProxySort = types.int.val(0, ctx=ctx)
        zero = types.int.val(0, ctx=ctx)
        for fact in self.relation.facts(ctx):
            cond = _fact_cond(fact=fact, predicate=self.predicate, ctx=ctx)

            if self.selector is None:
                base: ProxySort = types.int.val(1, ctx=ctx)
            else:
                base = self.selector.evaluate(row=fact.args, ctx=ctx)

            quantifiers = getattr(fact, "quantifiers", ())
            if not quantifiers:
                contrib = if_expr(cond, base, zero, ctx=ctx)
            else:
                if self.selector is not None:
                    bound_vars = [q.bound_var for q in quantifiers]
                    if _expr_uses_any_bound_var(base, bound_vars):
                        raise UnsupportedError(
                            "sum(selector) where selector depends on the loop "
                            "variable is not supported in v1; see "
                            "docs/todos/quantified-aggregates.md"
                        )
                # A quantified fact stands for one call per loop index, so its
                # contribution is `base` times the product of the loop trip
                # counts. `cond` may mention the loops' bound variables (a call
                # under `if row.consented:` inside the loop), and those stay
                # *free* here: this is a term inside a contract formula, not a
                # standalone assertion, so there is nowhere to bind them that
                # would mean what we want.
                #
                # For counts and nonnegative constant selectors, leaving the
                # indices free makes the term conservative in both
                # directions. Every obligation is discharged by checking
                # `given and not guarantee` unsatisfiable, so a free variable is
                # read universally: the guarantee must hold for *every* value of
                # each bound variable, in range or not. For an upper bound the
                # solver picks an index making `cond` true, demanding the full
                # product — an over-approximation of the real count. For a
                # lower bound it picks an index making `cond` false, collapsing
                # the term to zero, so the bound only discharges when it is
                # already met without this fact. Neither direction can prove
                # more than the truth.
                #
                # This assumes eval_for refuses loop-carried state: an
                # iteration depends on its row and loop-invariant values, not
                # values changed by previous iterations. Also preserve:
                #   * each for-loop frame gets a fresh `z3.Int` (see
                #     `_eval_stmt.eval_for`), so bound variables from different
                #     loops cannot be conflated;
                #   * the term is never wrapped in `ForAll`. Binding it would
                #     turn the upper-bound case into `product` unconditionally
                #     and, worse, the lower-bound case into a claim about a
                #     universally quantified body — neither is the reading the
                #     soundness argument above depends on.
                # `docs/explanation/symbolic-iteration.md` documents the
                # resulting precision boundary for contract authors.
                product_z3: z3.ArithRef = z3.IntVal(1)
                enclosing: list[Any] = []
                dependent_bounds: list[z3.BoolRef] = []
                for q in quantifiers:
                    if _expr_uses_any_bound_var(
                        q.upper, [outer.bound_var for outer in enclosing]
                    ):
                        # An inner result's length is constrained only for
                        # reached outer iterations. Outside those bounds the
                        # result function is deliberately unconstrained.
                        dependent_bounds.extend(
                            outer.bounds_expr() for outer in enclosing
                        )
                    product_z3 = product_z3 * q.upper
                    enclosing.append(q)
                if dependent_bounds:
                    cond = types.bool(z3.And(cond.expr, *dependent_bounds))
                product: ProxySort = types.int(expr=product_z3)
                multiplied = base.m_mul(product, ctx=ctx)
                contrib = if_expr(cond, multiplied, zero, ctx=ctx)

            total = total.m_add(contrib, ctx=ctx)
        return total


@dataclass(frozen=True)
class ComparisonSpec(ContractSpec):
    left: SumSpec
    op: str
    right: Any

    def solve(self, *, ctx: Any) -> BoolSort:
        left = self.left.value(ctx=ctx)
        right = _as_proxy(self.right, ctx=ctx)
        if self.op == "<=":
            return left.m_le(right, ctx=ctx)
        if self.op == "<":
            return left.m_lt(right, ctx=ctx)
        if self.op == ">=":
            return left.m_ge(right, ctx=ctx)
        if self.op == ">":
            return left.m_gt(right, ctx=ctx)
        raise RuntimeError("unreachable")


@dataclass(frozen=True)
class SharedValueSpec(ContractSpec):
    left_relation: Relation
    left_filter: LambdaSpec | None
    right_relation: Relation
    right_filter: LambdaSpec | None
    key: LambdaSpec

    def solve(self, *, ctx: Any) -> BoolSort:
        # v1: shares_value across quantified relations not supported
        all_facts = self.left_relation.facts(ctx) + self.right_relation.facts(ctx)
        for fact in all_facts:
            if getattr(fact, "quantifiers", ()):
                raise UnsupportedError(
                    "shares_value across quantified relations is not "
                    "supported in v1; see docs/todos/quantified-shares-value.md"
                )
        clauses = []
        for left in self.left_relation.facts(ctx):
            left_cond = _fact_cond(fact=left, predicate=self.left_filter, ctx=ctx)
            left_key = self.key.evaluate(row=left.args, ctx=ctx)
            for right in self.right_relation.facts(ctx):
                right_cond = _fact_cond(
                    fact=right,
                    predicate=self.right_filter,
                    ctx=ctx,
                )
                right_key = self.key.evaluate(row=right.args, ctx=ctx)
                clauses.append(
                    and_expr(
                        left_cond,
                        right_cond,
                        left_key.m_eq(right_key, ctx=ctx),
                        ctx=ctx,
                    )
                )
        return or_expr(*clauses, ctx=ctx)


def _fact_cond(*, fact: Any, predicate: LambdaSpec | None, ctx: Any) -> BoolSort:
    cond = cast(BoolSort, fact.cond)
    if predicate is None:
        return cond
    return and_expr(
        cond,
        predicate.evaluate(row=fact.args, ctx=ctx).m_bool(ctx=ctx),
        ctx=ctx,
    )


def _wrap_with_quantifiers(
    fact: Any,
    body: BoolSort,
    *,
    ctx: Any,
) -> BoolSort:
    """Wrap a relation body in ForAll over fact.quantifiers.

    Short-circuits when fact has no quantifiers (existing behavior preserved).
    """
    quantifiers = getattr(fact, "quantifiers", ())
    if not quantifiers:
        return body
    body_z3 = body.expr
    bound_vars = [q.bound_var for q in quantifiers]
    bounds_parts = [q.bounds_expr() for q in quantifiers]
    bounds = z3.And(*bounds_parts) if len(bounds_parts) > 1 else bounds_parts[0]
    wrapped = z3.ForAll(bound_vars, z3.Implies(bounds, body_z3))
    return types.bool(wrapped)


def _has_column_positions(code: CodeType) -> bool:
    """Whether ``code`` carries any real column span in its location table."""
    return any(
        col is not None and end_col is not None and col != end_col
        for _lineno, _end_lineno, col, end_col in code.co_positions()
    )


def _spans_within(code: CodeType, node: ast.Lambda) -> bool:
    """Whether every instruction of ``code`` came from inside ``node``'s source.

    ``co_positions`` also yields synthetic entries -- ``COPY_FREE_VARS`` has no
    position at all, and ``RESUME``/``RETURN_VALUE`` carry a zero-width marker
    at column 0 -- so only spans naming real source text are considered.
    """
    start = (node.lineno, node.col_offset)
    end = (node.end_lineno, node.end_col_offset)
    for lineno, end_lineno, col, end_col in code.co_positions():
        if lineno is None or col is None or end_col is None or col == end_col:
            continue
        if not (start <= (lineno, col) and (end_lineno, end_col) <= end):
            return False
    return True


@dataclass(frozen=True)
class LambdaSpec:
    arg_name: str
    body: ast.expr
    env: dict[str, Any]

    @classmethod
    def from_callable(cls, func: Callable[[Any], Any]) -> LambdaSpec:
        # Parse the whole defining file rather than inspect.getsourcelines(func):
        # for a lambda passed as a call argument on its own continuation line,
        # getsourcelines returns only the lambda's first physical line, silently
        # truncating multi-line bodies (e.g. a chained ``and``). Locating the
        # node in the full module by its first line number keeps the body whole.
        try:
            all_lines, _ = inspect.findsource(func)
        except (OSError, TypeError) as exc:
            # ``exec``-defined, ``-c``, stdin, or ``.pyc``-only source. Not an
            # UnsupportedError by itself, so it would escape prove() as a
            # traceback (#167).
            raise UnsupportedError(
                "could not read the source of a relation lambda; predicates "
                "must be lambdas defined in a file on disk"
            ) from exc
        module = ast.parse("".join(all_lines))
        code = func.__code__
        lambdas = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Lambda) and node.lineno == code.co_firstlineno
        ]
        if not lambdas:
            # A ``def`` passed where a lambda is expected, or a file edited on
            # disk since import. Distinct from ambiguity below (#167).
            raise UnsupportedError(
                "could not find lambda source on its line; relation predicates "
                "must be lambdas (not def-defined functions), and the file on "
                "disk must be the one that was imported"
            )
        if len(lambdas) > 1:
            # Two lambdas can share a line -- ``rel.where(...).all(...)`` is the
            # idiomatic one-liner -- so the line number alone does not identify
            # a body. Every instruction the compiler emitted for this code
            # object came from inside this lambda's own source span, so keep
            # only the candidates that contain all of them.
            if not _has_column_positions(code):
                # Under ``-X no_debug_ranges`` / ``PYTHONNODEBUGRANGES`` the
                # positions carry no columns, every candidate survives, and
                # the refusal below would blame the contract (#167).
                raise UnsupportedError(
                    "cannot tell same-line lambdas apart: the code object has "
                    "no column positions (usually -X no_debug_ranges or "
                    "PYTHONNODEBUGRANGES, at run or compile time); run with "
                    "column positions or put each lambda on its own line"
                )
            lambdas = [node for node in lambdas if _spans_within(code, node)]
            if not lambdas:
                # Every same-line candidate was rejected: the bytecode
                # positions fit none of their spans. Not nesting, so say so.
                raise UnsupportedError(
                    "could not match a relation lambda's bytecode positions to "
                    "any lambda on its source line; is the file on disk the "
                    "one that was imported?"
                )
        if len(lambdas) != 1:
            # Silently compiling a neighbouring lambda's body would mean a
            # contract that reads correctly and proves something weaker.
            raise UnsupportedError(
                "could not locate lambda source unambiguously; a lambda nested "
                "inside another lambda on the same line is not supported"
            )
        lambda_node = lambdas[0]
        if len(lambda_node.args.args) != 1:
            raise UnsupportedError("effect lambdas must have exactly one argument")
        closure = inspect.getclosurevars(func)
        env = {**closure.globals, **closure.nonlocals}
        return cls(
            arg_name=lambda_node.args.args[0].arg,
            body=lambda_node.body,
            env=env,
        )

    def fields(self) -> frozenset[str]:
        """Every ``e.<field>`` this predicate reads off a matched fact.

        Attribute access on anything else --- a ``Row`` class\'s column
        (``UserRow.email``), a module --- is not a field of the fact and is
        left alone.
        """

        return frozenset(
            node.attr
            for node in ast.walk(self.body)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == self.arg_name
        )

    def evaluate(self, *, row: dict[str, ProxySort], ctx: Any) -> ProxySort:
        # Relations are checked after the entry body has returned. Its exit
        # state must not short-circuit a pure predicate. These predicates are
        # encoded after the theorem's ordinary exception checks, so a possible
        # new error must refuse here rather than append an unchecked exception.
        predicate_ctx = ctx.make_child().evolve(returns=Layer(), exceptions=Layer())
        result = _ExpressionCompiler(
            arg_name=self.arg_name,
            row=row,
            env=self.env,
            ctx=predicate_ctx,
        ).eval(self.body)
        for exc in predicate_ctx.exceptions.layer:
            if not z3.is_false(z3.simplify(exc.cond.expr)):
                raise UnsupportedError("effect lambda may raise an exception", exc.name)
        for expected in predicate_ctx.expected.layer:
            if not z3.is_true(z3.simplify(expected.expr)):
                raise UnsupportedError("effect lambda has an unchecked obligation")
        return result


@dataclass(frozen=True)
class _ExpressionCompiler:
    arg_name: str
    row: dict[str, ProxySort]
    env: dict[str, Any]
    ctx: Any

    def eval(self, node: ast.AST) -> ProxySort:
        if isinstance(node, ast.Constant):
            return cast(ProxySort, _as_proxy(node.value, ctx=self.ctx))
        if isinstance(node, ast.List):
            return _list_value([self.eval(item) for item in node.elts], ctx=self.ctx)
        if isinstance(node, ast.Name):
            if node.id == self.arg_name:
                raise UnsupportedError("bare effect row is not supported")
            if node.id in self.env:
                return cast(ProxySort, _as_proxy(self.env[node.id], ctx=self.ctx))
            raise UnsupportedError("unknown name in effect lambda", node.id)
        if isinstance(node, ast.Attribute):
            return self._eval_attribute(node)
        if isinstance(node, ast.Compare):
            return self._eval_compare(node)
        if isinstance(node, ast.BoolOp):
            return self._eval_bool_op(node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self.eval(node.operand).m_not(ctx=self.ctx)
        if isinstance(node, ast.BinOp):
            return self._eval_bin_op(node)
        if isinstance(node, ast.Call):
            return self._eval_call(node)
        raise UnsupportedError(
            "unsupported expression in effect lambda",
            type(node).__name__,
        )

    def _eval_attribute(self, node: ast.Attribute) -> ProxySort:
        if isinstance(node.value, ast.Name) and node.value.id == self.arg_name:
            if node.attr not in self.row:
                raise UnsupportedError("unknown effect field", node.attr)
            return self.row[node.attr]
        raise UnsupportedError("only effect row attributes are supported")

    def _eval_compare(self, node: ast.Compare) -> ProxySort:
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise UnsupportedError("chained comparisons are not supported")
        left = self.eval(node.left)
        right = self.eval(node.comparators[0])
        op = node.ops[0]

        # Column-ref equality dispatch
        left_is_col = getattr(left, "is_column_ref", False)
        right_is_col = getattr(right, "is_column_ref", False)
        if isinstance(op, (ast.Eq, ast.NotEq)) and (left_is_col or right_is_col):
            col_proxy = cast(Any, left if left_is_col else right)
            other = right if left_is_col else left
            result = _compare_column_ref(other, col_proxy.column, ctx=self.ctx)
            if isinstance(op, ast.NotEq):
                return result.m_not(ctx=self.ctx)
            return result

        if isinstance(op, ast.Eq):
            return left.m_eq(right, ctx=self.ctx)
        if isinstance(op, ast.NotEq):
            return left.m_ne(right, ctx=self.ctx)
        if isinstance(op, ast.Lt):
            return left.m_lt(right, ctx=self.ctx)
        if isinstance(op, ast.LtE):
            return left.m_le(right, ctx=self.ctx)
        if isinstance(op, ast.Gt):
            return left.m_gt(right, ctx=self.ctx)
        if isinstance(op, ast.GtE):
            return left.m_ge(right, ctx=self.ctx)
        if isinstance(op, ast.In):
            return right.m_contains(left, ctx=self.ctx)
        if isinstance(op, ast.NotIn):
            return right.m_contains(left, ctx=self.ctx).m_not(ctx=self.ctx)
        raise UnsupportedError("unsupported comparison in effect lambda")

    def _eval_bool_op(self, node: ast.BoolOp) -> ProxySort:
        if isinstance(node.op, ast.And):
            op = "and"
        elif isinstance(node.op, ast.Or):
            op = "or"
        else:
            raise RuntimeError("unreachable")
        return eval_boolean_operands(
            op=op,
            values=node.values,
            evaluate=lambda value, ctx: replace(self, ctx=ctx).eval(value),
            ctx=self.ctx,
        )

    def _eval_bin_op(self, node: ast.BinOp) -> ProxySort:
        left = self.eval(node.left)
        right = self.eval(node.right)
        if isinstance(node.op, ast.Add):
            return left.m_add(right, ctx=self.ctx)
        if isinstance(node.op, ast.Sub):
            return left.m_sub(right, ctx=self.ctx)
        raise UnsupportedError("unsupported binary operation in effect lambda")

    def _eval_call(self, node: ast.Call) -> ProxySort:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "len"
            and len(node.args) == 1
            and not node.keywords
        ):
            return self.eval(node.args[0]).m_len(ctx=self.ctx)
        if isinstance(node.func, ast.Attribute) and node.func.attr in _STR_METHODS:
            return self._eval_str_method(node, node.func)
        raise UnsupportedError("unsupported call in effect lambda")

    def _eval_str_method(self, node: ast.Call, func: ast.Attribute) -> ProxySort:
        if node.keywords or len(node.args) != 1:
            raise UnsupportedError(
                "string method in effect lambda takes one positional argument",
                func.attr,
            )
        obj = self.eval(func.value)
        arg = self.eval(node.args[0])
        method = obj.methods.get(func.attr)
        if method is None:
            raise UnsupportedError("unsupported method in effect lambda", func.attr)
        return method.with_obj(obj).m_call(arg, ctx=self.ctx)


@dataclass(frozen=True)
class _ColumnRefProxy:
    """Sentinel wrapper for ColumnRef in lambda compilation."""

    column: ColumnRef
    ctx: Any

    @property
    def is_column_ref(self) -> bool:
        return True


def _as_proxy(value: Any, *, ctx: Any) -> Any:
    from clauz3.row import ColumnRef

    if isinstance(value, ProxySort):
        return value
    if isinstance(value, ColumnRef):
        return _ColumnRefProxy(column=value, ctx=ctx)
    if isinstance(value, bool):
        return types.bool.val(value, ctx=ctx)
    if isinstance(value, int):
        return types.int.val(value, ctx=ctx)
    if isinstance(value, float):
        return types.float.val(value, ctx=ctx)
    if isinstance(value, str):
        return types.str.val(value, ctx=ctx)
    if isinstance(value, list):
        return _list_value([_as_proxy(item, ctx=ctx) for item in value], ctx=ctx)
    if isinstance(value, tuple):
        if not value:
            return UntypedVarTupleSort()
        return types.tuple.val([_as_proxy(item, ctx=ctx) for item in value], ctx=ctx)
    if isinstance(value, set):
        if not value:
            return UntypedSetSort()
        return types.set.val([_as_proxy(item, ctx=ctx) for item in value], ctx=ctx)
    if isinstance(value, dict):
        result: DictSort = UntypedDictSort()
        for key, item in value.items():
            result = result.m_setitem(
                key=_as_proxy(key, ctx=ctx), value=_as_proxy(item, ctx=ctx), ctx=ctx
            )
        return result
    raise UnsupportedError("unsupported contract literal", type(value).__name__)


def _compare_column_ref(arg_proxy: Any, column_ref: ColumnRef, *, ctx: Any) -> BoolSort:
    """Structural match: does arg_proxy's symbolic value have the shape
    <column_ref.field selector>(array_select(<query of column_ref.schema>, ?))?

    Returns BoolSort.val(True) on match, BoolSort.val(False) otherwise.
    """
    from clauz3._vendor.deal_solver._proxies._row import z3_datatype_for_row

    expr = arg_proxy.expr if hasattr(arg_proxy, "expr") else arg_proxy
    if not isinstance(expr, z3.ExprRef):
        return types.bool.val(False, ctx=ctx)

    # 1) Top-level must be a single-argument function application matching
    #    the field accessor name.
    if expr.num_args() != 1:
        return types.bool.val(False, ctx=ctx)
    if expr.decl().name() != column_ref.field:
        return types.bool.val(False, ctx=ctx)

    # 2) The argument should be a Select(array, index).
    inner = expr.arg(0)
    if inner.decl().kind() != z3.Z3_OP_SELECT:
        return types.bool.val(False, ctx=ctx)

    # 3) The array's range sort should match the schema's Z3 datatype.
    array_expr = inner.arg(0)
    if not z3.is_array(array_expr):
        return types.bool.val(False, ctx=ctx)
    target_dt = z3_datatype_for_row(column_ref.schema)
    if array_expr.sort().range() != target_dt:
        return types.bool.val(False, ctx=ctx)

    return types.bool.val(True, ctx=ctx)


def _list_value(values: list[ProxySort], *, ctx: Any) -> ProxySort:
    if not values:
        return UntypedListSort()
    return types.list.val(values, ctx=ctx)


def _expr_uses_any_bound_var(proxy: Any, bound_vars: list[Any]) -> bool:
    """Return True if proxy's Z3 expression references any of the given bound vars.

    >>> import z3
    >>> i = z3.Int('i')
    >>> arr = z3.Array('arr', z3.IntSort(), z3.IntSort())
    >>> val = z3.Select(arr, i)
    >>> _expr_uses_any_bound_var(val, [i])
    True
    >>> _expr_uses_any_bound_var(z3.IntVal(42), [i])
    False
    """
    expr = proxy.expr if hasattr(proxy, "expr") else proxy
    if not isinstance(expr, z3.ExprRef):
        return False

    def _walk(e: z3.ExprRef) -> bool:
        for bv in bound_vars:
            if e.eq(bv):
                return True
        return any(_walk(c) for c in e.children())

    return _walk(expr)
