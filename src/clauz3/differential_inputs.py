"""Bounded, distinct concrete inputs from the antecedent of a successful proof.

The solver chooses entry arguments and trusted return values; no trusted body
is executed. Blocking clauses mention only those inputs visible in a concrete
run, never the unconstrained tail of a query's backing array.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, cast

import z3

from clauz3._vendor.deal_solver._context import FactInfo
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import (
    DictSort,
    ProxySort,
    SetSort,
    UntypedDictSort,
    UntypedListSort,
    UntypedSetSort,
    UntypedVarTupleSort,
    VarTupleSort,
)
from clauz3._vendor.deal_solver._proxies._row import (
    QueryResultSort,
    RowClassSort,
    RowSort,
)
from clauz3._vendor.deal_solver._strings import is_string_expr, string_val, string_value

if TYPE_CHECKING:
    from clauz3.prover import ProofObservation

Substitutions = tuple[tuple[z3.ExprRef, z3.ExprRef], ...]


class UnsupportedInputs(ValueError):
    """The harness cannot faithfully concretize this proof's inputs."""


class ModelGenerationError(RuntimeError):
    """Z3 could not decide whether another concrete input exists."""


@dataclass(frozen=True)
class ModelEffect:
    fact_index: int
    name: str
    qualified_name: str
    arguments: dict[str, object]
    result: object


@dataclass(frozen=True)
class ModelCase:
    model: z3.ModelRef
    arguments: dict[str, object]
    effects: tuple[ModelEffect, ...]
    limitations: tuple[str, ...] = ()


def _substitute(expr: z3.ExprRef, substitutions: Substitutions) -> z3.ExprRef:
    return z3.substitute(expr, *substitutions) if substitutions else expr


def _integer(model: z3.ModelRef, expr: z3.ExprRef) -> int:
    value = model.eval(expr, model_completion=True)
    if not z3.is_int_value(value):
        raise UnsupportedInputs(f"expected a concrete integer, got {value}")
    return cast(int, value.as_long())


def _decode_expr(expr: z3.ExprRef, model: z3.ModelRef) -> object:
    value = model.eval(expr, model_completion=True)
    if z3.is_true(value):
        return True
    if z3.is_false(value):
        return False
    if z3.is_int_value(value):
        return cast(int, value.as_long())
    if is_string_expr(value):
        try:
            return string_value(value)
        except UnsupportedError as error:
            raise UnsupportedInputs(str(error)) from error
    if z3.is_fp_value(value):
        if value.isNaN():
            return float("nan")
        if value.isInf():
            return float("-inf") if value.isNegative() else float("inf")
        if value.sort() != z3.Float64():
            raise UnsupportedInputs(f"unsupported floating point sort: {value.sort()}")
        bits = model.eval(z3.fpToIEEEBV(value), model_completion=True).as_long()
        return cast(float, struct.unpack(">d", int(bits).to_bytes(8, "big"))[0])
    if z3.is_rational_value(value):
        rational = Fraction(value.numerator_as_long(), value.denominator_as_long())
        concrete = float(rational)
        if Fraction.from_float(concrete) != rational:
            raise UnsupportedInputs(f"real input {value} is not an exact Python float")
        return concrete
    raise UnsupportedInputs(f"cannot decode model value {value} of sort {value.sort()}")


def _decode_row(expr: z3.ExprRef, schema: type, model: z3.ModelRef) -> object:
    # The retained expression's datatype survives per-proof cache isolation.
    # Looking up a fresh datatype by schema here could mint a different sort.
    datatype = expr.sort()
    fields = {
        name: _decode_expr(datatype.accessor(0, i)(expr), model)
        for i, name in enumerate(schema.__annotations__)
    }
    return schema(**fields)


def _finite_array(
    expr: z3.ExprRef, model: z3.ModelRef
) -> tuple[z3.ExprRef, list[z3.ExprRef]]:
    value = model.eval(expr, model_completion=True)
    keys = []
    while z3.is_store(value):
        keys.append(value.arg(1))
        value = value.arg(0)
    if not z3.is_const_array(value):
        raise UnsupportedInputs("cannot enumerate a nonfinite dictionary or set model")
    return value.arg(0), keys


def concretize(
    value: ProxySort,
    model: z3.ModelRef,
    substitutions: Substitutions = (),
    *,
    max_rows: int | None = None,
) -> object:
    """Decode a retained symbolic value without executing any Python source."""
    if isinstance(value, RowClassSort):
        return value.schema
    if isinstance(value, UntypedDictSort):
        return {}
    if isinstance(value, UntypedListSort):
        return []
    if isinstance(value, UntypedVarTupleSort):
        return ()
    if isinstance(value, UntypedSetSort):
        return set()
    expr = _substitute(value.expr, substitutions)
    if isinstance(value, QueryResultSort):
        size = _integer(model, _substitute(value.length_expr, substitutions))
        if size < 0 or (max_rows is not None and size > max_rows):
            raise UnsupportedInputs(f"query length {size} is outside 0..{max_rows}")
        return [
            _decode_row(z3.Select(expr, i), value.row_schema, model)
            for i in range(size)
        ]
    if isinstance(value, RowSort):
        return _decode_row(expr, value.schema, model)
    if isinstance(value, VarTupleSort):
        size = _integer(model, z3.Length(expr))
        if size < 0 or (max_rows is not None and size > max_rows):
            raise UnsupportedInputs(f"sequence length {size} is outside 0..{max_rows}")
        items = [
            concretize(value.subtypes[0].wrap(expr[i]), model, max_rows=max_rows)
            for i in range(size)
        ]
        return tuple(items) if value.type_name == "tuple" else items
    if isinstance(value, (DictSort, SetSort)):
        default, keys = _finite_array(expr, model)
        if isinstance(value, SetSort):
            if not z3.is_false(default):
                raise UnsupportedInputs("cannot enumerate an infinite set model")
            return {
                concretize(value.subtypes[0].wrap(key), model)
                for key in keys
                if z3.is_true(model.eval(z3.Select(expr, key)))
            }
        datatype = default.sort()
        exists, item = datatype.accessor(0, 0), datatype.accessor(0, 1)
        if not z3.is_false(model.eval(exists(default))):
            raise UnsupportedInputs("cannot enumerate an infinite dictionary model")
        return {
            concretize(value.subtypes[0].wrap(key), model): concretize(
                value.subtypes[1].wrap(item(z3.Select(expr, key))), model
            )
            for key in keys
            if z3.is_true(model.eval(exists(z3.Select(expr, key))))
        }
    return _decode_expr(expr, model)


def _bindings(
    fact: FactInfo, model: z3.ModelRef, max_rows: int
) -> Iterator[Substitutions]:
    bindings: list[Substitutions] = [()]
    for quantifier in fact.quantifiers:
        expanded: list[Substitutions] = []
        for previous in bindings:
            lower = _integer(model, _substitute(quantifier.lower, previous))
            upper = _integer(model, _substitute(quantifier.upper, previous))
            if not 0 <= lower <= max_rows or not 0 <= upper <= max_rows:
                raise UnsupportedInputs("loop bounds exceed the configured row bound")
            expanded.extend(
                (*previous, (quantifier.bound_var, z3.IntVal(index)))
                for index in range(lower, upper)
            )
        if len(expanded) > 10_000:
            raise UnsupportedInputs("quantified effect expansion exceeds 10000 calls")
        bindings = expanded
    yield from bindings


def _active(
    fact: FactInfo, model: z3.ModelRef, substitutions: Substitutions = ()
) -> bool:
    value = model.eval(
        _substitute(fact.cond.expr, substitutions), model_completion=True
    )
    if not z3.is_true(value) and not z3.is_false(value):
        raise UnsupportedInputs(
            f"effect condition did not evaluate to a Boolean: {value}"
        )
    return bool(z3.is_true(value))


def _effects(
    observation: ProofObservation, model: z3.ModelRef, max_rows: int
) -> tuple[ModelEffect, ...]:
    # Facts are stored by static call site, but replay must consume returning
    # calls in iteration order. Each enclosing loop contributes its position
    # in the fact stream and then its concrete iteration index. Compute loop
    # positions from all facts, including those with inactive conditions.
    first_fact: dict[int, int] = {}
    for index, fact in enumerate(observation.effects):
        for quantifier in fact.quantifiers:
            first_fact.setdefault(quantifier.bound_var.get_id(), index)
    effects: list[tuple[tuple[int, ...], ModelEffect]] = []
    for index, fact in enumerate(observation.effects):
        for substitutions in _bindings(fact, model, max_rows):
            if not _active(fact, model, substitutions):
                continue
            execution_key = (
                *(
                    component
                    for bound_var, concrete_index in substitutions
                    for component in (
                        first_fact[bound_var.get_id()],
                        cast(int, concrete_index.as_long()),
                    )
                ),
                index,
            )
            effects.append(
                (
                    execution_key,
                    ModelEffect(
                        fact_index=index,
                        name=fact.name,
                        qualified_name=fact.qualified_name,
                        arguments={
                            name: concretize(value, model, substitutions)
                            for name, value in fact.args.items()
                        },
                        result=(
                            concretize(
                                fact.result, model, substitutions, max_rows=max_rows
                            )
                            if fact.result is not None
                            else None
                        ),
                    ),
                )
            )
    return tuple(effect for _key, effect in sorted(effects, key=lambda pair: pair[0]))


def _projection(
    value: ProxySort, model: z3.ModelRef, substitutions: Substitutions = ()
) -> Iterator[z3.ExprRef]:
    if isinstance(value, QueryResultSort):
        length = _substitute(value.length_expr, substitutions)
        array = _substitute(value.array_expr, substitutions)
        yield length
        for index in range(_integer(model, length)):
            yield z3.Select(array, index)
    else:
        yield _substitute(value.expr, substitutions)


def _length(value: ProxySort) -> z3.ArithRef | None:
    if isinstance(value, QueryResultSort):
        return value.length_expr
    if value.type_name in {"list", "tuple"}:
        return z3.Length(value.expr)
    return None


def _scalar_hints(value: ProxySort, max_rows: int) -> Iterator[z3.ExprRef]:
    if isinstance(value, QueryResultSort):
        datatype = value.array_expr.sort().range()
        for index in range(max_rows):
            row = z3.Select(value.array_expr, index)
            for column in range(datatype.constructor(0).arity()):
                yield datatype.accessor(0, column)(row)
    elif isinstance(value, RowSort):
        datatype = value.expr.sort()
        for column in range(datatype.constructor(0).arity()):
            yield datatype.accessor(0, column)(value.expr)
    elif _length(value) is None:
        yield value.expr


def _bounded_sequence(
    value: VarTupleSort, max_rows: int
) -> tuple[list[z3.BoolRef], list[z3.ExprRef]]:
    """Expose bounded sequence elements as fresh scalar solver variables.

    Z3's nested sequence theory can return unknown for ``Seq(String)`` after
    blocking a whole sequence model. Every sequence within the configured
    bound has this Unit/Concat representation, so this adds structure without
    discarding any bounded input or weakening any proof assumption.
    """
    slots = [
        z3.FreshConst(value.expr.sort().basis(), prefix="differential_item")
        for _ in range(max_rows)
    ]
    cases = []
    for size in range(max_rows + 1):
        if size == 0:
            sequence = z3.Empty(value.expr.sort())
        elif size == 1:
            sequence = z3.Unit(slots[0])
        else:
            sequence = z3.Concat(*[z3.Unit(slot) for slot in slots[:size]])
        cases.append(z3.And(z3.Length(value.expr) == size, value.expr == sequence))
    constraints = [z3.Or(*cases)]
    scalars = []
    for slot in slots:
        child = value.subtypes[0].wrap(slot)
        if isinstance(child, VarTupleSort):
            nested_constraints, nested_scalars = _bounded_sequence(child, max_rows)
            constraints.extend(nested_constraints)
            scalars.extend(nested_scalars)
        else:
            scalars.extend(_scalar_hints(child, max_rows))
    return constraints, scalars


def _edge_hint(expr: z3.ExprRef, iteration: int) -> z3.BoolRef | None:
    if is_string_expr(expr):
        values = (
            "",
            "a",
            "é",
            "😀",
            "\U00030000",
            "\U0010ffff",
            "\ud800",
            "-1",
            "0",
            "1",
            " 1 ",
            "no",
            "\x00",
        )
        return expr == string_val(values[iteration % len(values)], ctx=expr.ctx)
    if z3.is_int(expr):
        integers = (0, 1, -1, 2, -2, 10)
        return expr == integers[iteration % len(integers)]
    if z3.is_bool(expr):
        return expr == z3.BoolVal(iteration % 2 == 0)
    return None


def _preferred_model(
    solver: z3.Solver, shapes: list[list[z3.BoolRef]], edges: list[z3.BoolRef]
) -> z3.ModelRef | None:
    # Try other concrete sizes when the preferred one is exhausted or ruled
    # out by assumptions. Leaving sequence lengths unconstrained can itself
    # trigger Z3's incomplete nested-sequence theory.
    for shape in [*shapes, []]:
        solver.push()
        solver.add(*(shape + edges))
        status = solver.check()
        model = solver.model() if status == z3.sat else None
        solver.pop()
        if model is not None:
            return model
        solver.push()
        solver.add(*shape)
        status = solver.check()
        if status != z3.sat:
            reason = solver.reason_unknown()
            solver.pop()
            continue
        model = solver.model()
        # Keep independently feasible hints, so e.g. a positive integer
        # postcondition cannot prevent Boolean/Unicode variation in other fields.
        for hint in edges:
            solver.push()
            solver.add(hint)
            hinted_status = solver.check()
            if hinted_status == z3.sat:
                model = solver.model()
            solver.pop()
            if hinted_status == z3.sat:
                solver.add(hint)
        solver.pop()
        return model
    if status == z3.unknown:
        raise ModelGenerationError(f"input generation: {reason}")
    return None


def model_cases(
    observation: ProofObservation,
    *,
    runs: int = 10,
    max_rows: int = 3,
    seed: int = 0,
    timeout_ms: int = 5000,
) -> Iterator[ModelCase]:
    """Generate at most ``runs`` distinct bounded models of the assumptions.

    Query lengths preferentially cover one, zero, then two rows, so a small
    budget exercises loop bodies. Hints are preferences: unsatisfiable hints
    are discarded, never proof assumptions. Exhausted finite inputs stop early.
    Legacy constant-valued quantified scalar returns remain correlated. Return
    expressions indexed by the loop are projected at each concrete binding.
    The caller must validate each boundary's postconditions on concrete inputs.
    Quantified query/container returns remain explicitly unsupported.
    """
    if runs < 1 or max_rows < 0 or timeout_ms < 1:
        raise ValueError("runs and timeout_ms must be positive; max_rows nonnegative")
    values = list(observation.arguments.values())
    limitations = []
    for fact in observation.effects:
        if fact.result is None:
            continue
        if fact.quantifiers:
            if fact.result.type_name not in {"str", "int", "bool", "float"}:
                raise UnsupportedInputs(
                    "container-returning trusted call "
                    f"{fact.qualified_name or fact.name} is quantified"
                )
            # A composite return could mention the loop index. It is decoded
            # and projected under each binding below, never hinted as a free
            # scalar outside its quantifier frame.
            if z3.is_const(fact.result.expr):
                limitations.append(
                    f"{fact.qualified_name or fact.name}: quantified returns share "
                    "a symbolic value across iterations"
                )
                values.append(fact.result)
        else:
            values.append(fact.result)
    # Propagate concrete size/edge hints before solving nested Unicode domains.
    # Otherwise Z3 can return unknown even for a singleton list of a literal
    # string. These tactics retain all assumptions and reconstruct the model.
    solver = z3.Then(
        "simplify", "propagate-values", "solve-eqs", "simplify", "smt"
    ).solver()
    solver.set(timeout=timeout_ms, random_seed=seed)
    solver.add(observation.assumptions)
    lengths = [length for value in values if (length := _length(value)) is not None]
    for length in lengths:
        solver.add(length >= 0, length <= max_rows)
    scalar_terms = []
    for value in values:
        if isinstance(value, (UntypedListSort, UntypedVarTupleSort)):
            continue
        if isinstance(value, VarTupleSort):
            constraints, scalars = _bounded_sequence(value, max_rows)
            solver.add(*constraints)
            scalar_terms.extend(scalars)
        else:
            scalar_terms.extend(_scalar_hints(value, max_rows))
    sizes = list(dict.fromkeys([min(1, max_rows), 0, *range(2, max_rows + 1)]))
    for iteration in range(runs):
        preferred_size = sizes[iteration % len(sizes)]
        size_order = [
            preferred_size,
            *[size for size in sizes if size != preferred_size],
        ]
        shapes = (
            [[length == size for length in lengths] for size in size_order]
            if lengths
            else [[]]
        )
        edges = [
            hint
            for offset, expr in enumerate(scalar_terms)
            if (hint := _edge_hint(expr, iteration + offset)) is not None
        ]
        model = _preferred_model(solver, shapes, edges)
        if model is None:
            return
        case = ModelCase(
            model=model,
            arguments={
                name: concretize(value, model, max_rows=max_rows)
                for name, value in observation.arguments.items()
            },
            effects=_effects(observation, model, max_rows),
            limitations=tuple(dict.fromkeys(limitations)),
        )
        projection = [
            expr
            for value in observation.arguments.values()
            for expr in _projection(value, model)
        ]
        for fact in observation.effects:
            if fact.result is None:
                continue
            for substitutions in _bindings(fact, model, max_rows):
                if _active(fact, model, substitutions):
                    projection.extend(_projection(fact.result, model, substitutions))
        solver.add(
            z3.Or(
                *[
                    expr != model.eval(expr, model_completion=True)
                    for expr in projection
                ]
            )
        )
        yield case
