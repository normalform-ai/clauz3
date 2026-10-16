from __future__ import annotations

import typing

import astroid
import z3

from clauz3.diagnostics import proof as proof_monitor
from astroid.exceptions import (
    AstroidBuildingError,
    AttributeInferenceError,
    InferenceError,
)

from ._ast import get_full_name, infer
from ._bool_op import BOOL_OPERATIONS as BOOL_OPERATIONS
from ._bool_op import eval_boolean_operands
from ._closure import check_static_closure_name
from ._context import Context
from ._context._trace import COMPREHENSION_BODY
from ._context._branch import merge_branch_layers
from ._context._trace import GUARDED_EXPRESSION
from ._exceptions import UnsupportedError
from ._funcs import FUNCTIONS
from ._proxies import (
    DictSort,
    FuncSort,
    LambdaSort,
    NoneSort,
    ProxySort,
    UntypedDictSort,
    UntypedListSort,
    UntypedVarTupleSort,
    and_expr,
    if_expr,
    or_expr,
    random_name,
    types,
)
from ._proxies._method import Method
from ._registry import HandlersRegistry


eval_expr: HandlersRegistry[ProxySort] = HandlersRegistry()

CONSTS: typing.Mapping[type, typing.Callable[..., ProxySort]]
CONSTS = {
    bool: types.bool.val,
    int: types.int.val,
    float: types.float.val,
    str: types.str.val,
}
COMAPARISON: typing.Mapping[str, str]
COMAPARISON = {
    "<": "__lt__",
    "<=": "__le__",
    ">": "__gt__",
    ">=": "__ge__",
    "==": "__eq__",
    "!=": "__ne__",
    "in": "in",
    "not in": "not in",
}
BIN_OPERATIONS: typing.Mapping[str, str]
BIN_OPERATIONS = {
    # math
    "+": "__add__",
    "-": "__sub__",
    "*": "__mul__",
    "/": "__truediv__",
    "//": "__floordiv__",
    "**": "__pow__",
    "%": "__mod__",
    "@": "__matmul__",
    # bitwise
    "&": "__and__",
    "|": "__or__",
    "^": "__xor__",
    "<<": "__lshift__",
    ">>": "__rshift__",
}


@eval_expr.register(astroid.Const)
def eval_const(node: astroid.Const, ctx: Context) -> ProxySort:
    t = type(node.value)
    converter = CONSTS.get(t)
    if not converter:
        raise UnsupportedError("unsupported constant", repr(node.value))
    return converter(node.value, ctx=ctx)


@eval_expr.register(astroid.BinOp)
def eval_bin_op(node: astroid.BinOp, ctx: Context) -> ProxySort:
    assert node.op
    left = eval_expr(node=node.left, ctx=ctx)
    right = eval_expr(node=node.right, ctx=ctx)
    return apply_bin_op(node.op, left, right, ctx=ctx)


def apply_bin_op(op: str, left: ProxySort, right: ProxySort, *, ctx: Context) -> ProxySort:
    """``left op right`` via the proxy dunder; shared with ``eval_aug_assign``."""
    op_name = BIN_OPERATIONS.get(op)
    assert op_name, "unsupported binary operator"
    method = left.m_getattr(op_name, ctx=ctx)
    return method.m_call(right, ctx=ctx)


@eval_expr.register(astroid.Compare)
def eval_compare(node: astroid.Compare, ctx: Context) -> ProxySort:
    # Python chained comparisons `a OP1 b OP2 c` mean `a OP1 b and b OP2 c`,
    # with each intermediate expression evaluated once. We reuse the
    # previously-evaluated right as the next left to preserve that.
    # A chain short-circuits like `and`: `c` is evaluated only when `a OP1 b`
    # held. Evaluating it in `ctx` would record its exceptions and effects as
    # if CPython had run it -- the same suppression route eval_bool_op closes.
    if len(node.ops) == 1:
        left = eval_expr(node=node.left, ctx=ctx)
        op, right_node = node.ops[0]
        right = eval_expr(node=right_node, ctx=ctx)
        return _compare_pair(op=op, left=left, right=right, ctx=ctx)

    with ctx.trace.guard(GUARDED_EXPRESSION):
        left = eval_expr(node=node.left, ctx=ctx)
    pairs: list[ProxySort] = []
    for op, right_node in node.ops:
        reached = and_expr(*pairs, ctx=ctx)
        if z3.is_false(z3.simplify(reached.expr)):
            return reached
        guard = and_expr(reached, ctx.interrupted.m_not(ctx=ctx), ctx=ctx)
        child = ctx.make_child()
        with ctx.trace.guard(GUARDED_EXPRESSION):
            right = eval_expr(node=right_node, ctx=child)
            # The comparison can itself raise (for example 1 < 'x'). Its
            # obligations must use the same guard as the right operand.
            pair = _compare_pair(op=op, left=left, right=right, ctx=child)
            pair = pair.m_bool(ctx=child)
        _require_unchanged_expression_scope(child)
        merge_branch_layers(branch=child, guard=guard, ctx=ctx)
        pairs.append(pair)
        left = right
    if not pairs:
        raise RuntimeError("unreachable")
    return and_expr(*pairs, ctx=ctx)


def _compare_pair(*, op: str, left: ProxySort, right: ProxySort, ctx: Context) -> ProxySort:
    op_name = COMAPARISON.get(op)
    if not op_name:
        raise UnsupportedError('unsupported comparison operator', op)
    return left.m_getattr(op_name, ctx=ctx).m_call(right, ctx=ctx)


def _require_unchanged_expression_scope(ctx: Context) -> None:
    # The trace guard catches writes in detached helper/lambda contexts too;
    # this local check remains a backstop for other future binding handlers.
    if ctx.scope.layer:
        raise UnsupportedError('scope changes inside a guarded expression are not supported')


@eval_expr.register(astroid.BoolOp)
def eval_bool_op(node: astroid.BoolOp, ctx: Context) -> ProxySort:
    return eval_boolean_operands(
        op=node.op,
        values=node.values,
        evaluate=lambda subnode, branch: eval_expr(node=subnode, ctx=branch),
        ctx=ctx,
    )


@eval_expr.register(astroid.List)
def eval_list(node: astroid.List, ctx: Context) -> ProxySort:
    if not node.elts:
        return ctx.ownership.fresh(UntypedListSort(), ctx=ctx)
    items = []
    for subnode in node.elts:
        item = eval_expr(node=subnode, ctx=ctx)
        ctx.ownership.share(item)
        items.append(item)
    return ctx.ownership.fresh(types.list.val(items, ctx=ctx), ctx=ctx)


@eval_expr.register(astroid.Set)
def eval_set(node: astroid.Set, ctx: Context) -> ProxySort:
    items = []
    for subnode in node.elts:
        item = eval_expr(node=subnode, ctx=ctx)
        ctx.ownership.share(item)
        items.append(item)
    return ctx.ownership.fresh(types.set.val(items, ctx=ctx), ctx=ctx)


@eval_expr.register(astroid.Dict)
def eval_dict(node: astroid.Dict, ctx: Context) -> ProxySort:
    container: DictSort = UntypedDictSort()
    for key_node, val_node in node.items:
        key = eval_expr(node=key_node, ctx=ctx)
        val = eval_expr(node=val_node, ctx=ctx)
        ctx.ownership.share(key)
        ctx.ownership.share(val)
        container = container.m_setitem(key=key, value=val, ctx=ctx)
    return ctx.ownership.fresh(container, ctx=ctx)


@eval_expr.register(astroid.Tuple)
def eval_tuple(node: astroid.Tuple, ctx: Context) -> ProxySort:
    if not node.elts:
        return UntypedVarTupleSort()
    items = []
    for subnode in node.elts:
        item = eval_expr(node=subnode, ctx=ctx)
        ctx.ownership.share(item)
        items.append(item)
    return types.tuple.val(items, ctx=ctx)


@eval_expr.register(astroid.ListComp)
def eval_list_comp(node: astroid.ListComp, ctx: Context) -> ProxySort:
    if len(node.generators) > 1:
        raise UnsupportedError("to many loops inside list compr")

    comp: astroid.Comprehension
    comp = node.generators[0]

    # Python evaluates the outer iterable in the caller. Only the filters and
    # body use discarded child scopes; their mutations cannot be propagated.
    items = eval_expr(node=comp.iter, ctx=ctx)
    with ctx.trace.guard(COMPREHENSION_BODY):
        if comp.ifs:
            items = _compr_apply_ifs(ctx=ctx, comp=comp, items=items)
        items = _compr_apply_body(node=node, ctx=ctx, comp=comp, items=items)
    return ctx.ownership.fresh(items, ctx=ctx)


def _compr_reject_effects(body_ctx: Context) -> None:
    """Fail closed on effects the comprehension encoding cannot model.

    The comprehension body/ifs are evaluated once in a child context with a
    symbolic index, so per-iteration trusted facts, preconditions, and
    exceptions cannot be merged back soundly. This holds even for a
    *non-aggregate* guarantee: without ``eval_for``'s quantifier frame, a
    single merged fact's path condition would not range over the N iterations,
    so it would model one occurrence instead of N. Lifting this restriction
    would mean building the same bound-variable/quantifier machinery
    ``eval_for`` uses; until then, discarding the facts silently would let any
    effect guarantee be defeated by wrapping the call in a comprehension, so we
    map to UnsupportedError (SKIP) instead.

    We read ``.layer`` (this child's own facts) rather than iterating
    ``body_ctx.facts`` (which walks into parents): a trusted call in the body
    goes through ``eval_call`` -> ``FuncSort.m_call``, which adds facts directly
    to the ``body_ctx`` passed in, so they land in this layer. Invariant: body
    effects must emit into ``body_ctx`` directly, not a deeper child. If a
    future refactor ever evaluates ``node.elt``/the ``ifs`` in a deeper child,
    facts could land below this layer and slip past the ``.layer``-only check.
    """
    raisable = [exc for exc in body_ctx.exceptions.layer if not exc.is_impossible]
    if body_ctx.facts.layer or body_ctx.expected.layer or raisable:
        raise UnsupportedError(
            "trusted calls with effects or preconditions inside "
            "comprehensions are not supported"
        )


def _compr_apply_ifs(
    ctx: Context,
    comp: astroid.Comprehension,
    items: ProxySort,
) -> ProxySort:
    if not isinstance(items, (types.list, types.tuple)):
        raise UnsupportedError(f"cannot iterate over {items.type_name}")
    one = z3.IntVal(1, ctx=ctx.z3_ctx)
    zero = z3.IntVal(0, ctx=ctx.z3_ctx)

    index = z3.Int(random_name("index"))
    body_ctx = ctx.make_child()
    body_ctx.scope.set(
        name=comp.target.name,
        value=items.m_getitem(types.int(index), ctx=ctx.make_child()),
    )

    conds = []
    for cond_node in comp.ifs:
        cond = eval_expr(node=cond_node, ctx=body_ctx)
        conds.append(cond.m_bool(ctx=ctx).expr)
    _compr_reject_effects(body_ctx)

    f = z3.RecFunction(
        random_name("compr_cond"),
        z3.IntSort(ctx=ctx.z3_ctx),
        items.sort(),
    )
    if_body = z3.If(
        z3.And(*conds),
        z3.Unit(items.m_getitem(types.int(index), ctx=ctx.make_child()).expr),
        z3.Empty(items.sort()),
    )
    z3.RecAddDefinition(
        f,
        index,
        z3.If(
            index == zero,
            if_body,
            f(index - one) + if_body,
        ),
    )
    expr = f(z3.Length(items.expr) - one)
    return types.list(expr=expr, subtypes=items.subtypes)


def _compr_apply_body(
    node: astroid.ListComp,
    ctx: Context,
    comp: astroid.Comprehension,
    items: ProxySort,
) -> ProxySort:
    if not isinstance(items, (types.list, types.tuple)):
        raise UnsupportedError(f"cannot iterate over {items.type_name}")
    one = z3.IntVal(1, ctx=ctx.z3_ctx)
    zero = z3.IntVal(0, ctx=ctx.z3_ctx)
    index = z3.Int(random_name("index"))
    body_ctx = ctx.make_child()
    body_ctx.scope.set(
        name=comp.target.name,
        value=items.m_getitem(types.int(index), ctx=ctx.make_child()),
    )
    body_ref = eval_expr(node=node.elt, ctx=body_ctx)
    _compr_reject_effects(body_ctx)
    ctx.ownership.share(body_ref)
    body_ref = body_ref.expr

    f = z3.RecFunction(
        random_name("compr_body"),
        z3.IntSort(ctx=ctx.z3_ctx),
        z3.SeqSort(body_ref.sort()),
    )
    z3.RecAddDefinition(
        f,
        [index],
        z3.If(
            index == zero,
            z3.Unit(body_ref),
            f(index - one) + z3.Unit(body_ref),
        ),
    )
    expr = f(z3.Length(items.expr) - one)
    return types.list(expr=expr, subtypes=items.subtypes)


@eval_expr.register(astroid.Subscript)
def eval_getitem(node: astroid.Subscript, ctx: Context) -> ProxySort:
    value_ref = eval_expr(node=node.value, ctx=ctx)

    if not isinstance(node.slice, astroid.Slice):
        item_ref = eval_expr(node=node.slice, ctx=ctx)
        if isinstance(item_ref, NoneSort):
            raise UnsupportedError('unsupported operation on None', 'index')
        return value_ref.m_getitem(item_ref, ctx=ctx)

    if node.slice.step:
        raise UnsupportedError("slice step is not supported")
    if node.slice.lower:
        lower_ref = eval_expr(node=node.slice.lower, ctx=ctx)
    else:
        lower_ref = types.int(z3.IntVal(0, ctx=ctx.z3_ctx))
    if node.slice.upper:
        upper_ref = eval_expr(node=node.slice.upper, ctx=ctx)
    else:
        upper_ref = value_ref.m_len(ctx=ctx)
    result = value_ref.get_slice(start=lower_ref, stop=upper_ref, ctx=ctx)
    return ctx.ownership.fresh(result, ctx=ctx)


@eval_expr.register(astroid.Name)
def eval_name(node: astroid.Name, ctx: Context) -> ProxySort:
    # resolve local vars
    value = ctx.scope.get(node.name)
    if value is not None:
        return value

    check_static_closure_name(node)

    # Do not drop unknown alternatives or select the first possible binding:
    # Python may call a trusted effect where that first binding is a pure model.
    inferred = _infer_static(node)
    if inferred:
        func = inferred[0]
        if not all(
            isinstance(candidate, (astroid.FunctionDef, astroid.ClassDef))
            for candidate in inferred
        ):
            raise UnsupportedError("cannot resolve callable target", node.name)
        if not all(candidate is func for candidate in inferred):
            raise UnsupportedError("ambiguous callable target", node.name)
        _, bindings = node.lookup(node.name)
        _check_callable_bindings(bindings, node.name, set())
        target_name = ".".join(get_full_name(func))
        registered = FUNCTIONS.get(target_name)
        if registered is not None:
            # Builtin constructors infer as ClassDef; use their actual binding,
            # never a model chosen only because the local name spells 'int'.
            return FuncSort(registered)
        if isinstance(func, astroid.FunctionDef) and func.body:
            if _is_contract_helper(func):
                raise UnsupportedError("no definition for", target_name)
            return FuncSort(func)
        if isinstance(func, astroid.ClassDef):
            from ._proxies._row import RowClassSort, row_schema_from_class_def

            row_schema = row_schema_from_class_def(func)
            if row_schema is not None:
                return RowClassSort(schema=row_schema)

    # Failed inference must not recover a different callable by its spelling.
    # Only genuinely unbound schema sentinels use the runtime Row-name lookup.
    _, bindings = node.lookup(node.name)
    if bindings:
        raise UnsupportedError("cannot resolve callable target", node.name)

    # resolve Row subclass names via sys.modules (for contract arguments)
    row_class = _resolve_row_class(node)
    if row_class is not None:
        from ._proxies._row import RowClassSort

        return RowClassSort(schema=row_class)

    raise UnsupportedError("cannot resolve name", node.name)


def _check_callable_bindings(
    bindings: typing.Iterable[astroid.NodeNG],
    name: str,
    seen: set[tuple[astroid.NodeNG, str]],
) -> None:
    """Check import edges whose ambiguity Astroid's inference can hide."""
    for binding in bindings:
        key = (binding, name)
        if key in seen:
            raise UnsupportedError("cyclic callable target", name)
        path = seen | {key}
        if isinstance(binding, (astroid.FunctionDef, astroid.ClassDef)):
            continue
        if isinstance(binding, astroid.ImportFrom):
            originals = [
                original for original, alias in binding.names
                if (alias or original) == name
            ]
            # `from layer import pure as f, effect as f` binds the last name
            # at runtime, but Astroid may return only the first candidate.
            if len(originals) != 1:
                raise UnsupportedError("ambiguous callable target", name)
            try:
                source = binding.do_import_module()
                members = source.getattr(originals[0])
            except (AstroidBuildingError, AttributeInferenceError, InferenceError) as exc:
                raise UnsupportedError("cannot resolve callable target", name) from exc
            _check_callable_bindings(members, originals[0], path)
            continue
        if isinstance(binding, astroid.AssignName) and isinstance(
            binding.parent, (astroid.Assign, astroid.AnnAssign)
        ):
            value = binding.parent.value
            if isinstance(value, astroid.Name):
                _, aliases = value.lookup(value.name)
                _check_callable_bindings(aliases, value.name, path)
                continue
        raise UnsupportedError("unsupported callable target binding", name)


def _is_contract_helper(func: astroid.FunctionDef) -> bool:
    """Return whether *func* is decorated by ``clauz3.spec.contract``.

    Contract helpers build declarative specs and must only run through the
    solver registered by ``@contract``. Inlining their Python body as an
    ordinary function can turn a registry miss into a vacuous guarantee.
    """
    if func.decorators is None:
        return False
    for decorator in func.decorators.nodes:
        # @contract(description=...) is still a contract boundary. Infer the
        # decorator's callee, not the closure returned by its configuration call.
        target = decorator.func if isinstance(decorator, astroid.Call) else decorator
        for definition in infer(target):
            if not isinstance(definition, astroid.FunctionDef):
                continue
            if ".".join(get_full_name(definition)) == "clauz3.spec.contract":
                return True
    return False


def _resolve_row_class(node: astroid.Name) -> type | None:
    """Try to resolve a Name node to a clauz3.Row subclass via sys.modules.

    Returns the class if exactly one Row subclass carries this name, otherwise
    None. Trusted modules are pre-loaded before proof evaluation, so any Row
    subclass with this name will be present in sys.modules at call time.

    This scan has no module context to work from, so a bare name shared by two
    trusted layers is genuinely ambiguous: picking the first match would decide
    which domain's schema a guarantee constrains by ``sys.modules`` ordering.
    Returning None instead raises "cannot resolve name" upstream, which the
    theorem records as a SKIP rather than a proof (issue #127).

    The scan itself is shared with the guarantee grammar checker
    (:func:`clauz3.row.row_classes_by_name`), so a schema name the checker
    accepts is looked up the same way here; only the policy differs -- the
    checker needs the name to denote *some* schema, evaluation needs exactly
    one.
    """
    from clauz3.row import row_classes_by_name

    candidates = row_classes_by_name(node.name)
    if len(candidates) != 1:
        return None
    return candidates[0]

def _infer_static(node: astroid.NodeNG) -> tuple[typing.Any, ...]:
    """Retain Uninferable results so partial inference cannot authorize lookup."""
    try:
        definitions = node.infer()
        if definitions is astroid.Uninferable:
            return (astroid.Uninferable,)
        return tuple(definitions)
    except (InferenceError, RecursionError):
        return ()


def _is_module_reference(node: astroid.NodeNG, ctx: Context) -> bool:
    """Allow only imported module paths, never calls or object properties."""
    prefixes = [node]
    while isinstance(node, astroid.Attribute):
        node = node.expr
        prefixes.append(node)
    if not isinstance(node, astroid.Name) or ctx.scope.get(node.name) is not None:
        return False
    check_static_closure_name(node)
    _, bindings = node.lookup(node.name)
    if not bindings or not all(
        _is_module_binding(binding, node.name) for binding in bindings
    ):
        return False
    # Check every prefix: an object property may itself return a module, but
    # looking through that property would skip its execution and effects.
    for prefix in reversed(prefixes):
        definitions = _infer_static(prefix)
        if not definitions or not all(
            isinstance(definition, astroid.Module) for definition in definitions
        ):
            return False
        if isinstance(prefix, astroid.Attribute):
            members = _module_members(prefix)
            if not members or not all(
                _is_module_binding(member, prefix.attrname) for member in members
            ):
                return False
    return True


def _is_module_binding(binding: astroid.NodeNG, name: str) -> bool:
    if isinstance(binding, astroid.Module):
        return True
    if isinstance(binding, astroid.Import):
        # Astroid can select the first of duplicate aliases, while Python
        # keeps the last. Refuse that ambiguous binding instead of guessing.
        return sum(
            (alias or original.split('.')[0]) == name
            for original, alias in binding.names
        ) == 1
    if isinstance(binding, astroid.ImportFrom):
        originals = [
            original for original, alias in binding.names
            if (alias or original) == name
        ]
        if len(originals) != 1:
            return False
        original = originals[0]
        try:
            source = binding.do_import_module()
            members = source.getattr(original)
        except (AstroidBuildingError, AttributeInferenceError, InferenceError):
            return False
        # ImportFrom can import a computed module-valued attribute. Only
        # actual submodules and explicit imports establish its identity;
        # further re-export chains require a model of their own.
        return bool(members) and all(
            isinstance(member, astroid.Module)
            or isinstance(member, astroid.Import) and _is_module_binding(member, original)
            for member in members
        )
    return False


def _module_members(node: astroid.Attribute) -> tuple[astroid.NodeNG, ...]:
    members = []
    for module in _infer_static(node.expr):
        if not isinstance(module, astroid.Module):
            return ()
        try:
            members.extend(module.getattr(node.attrname))
        except (AstroidBuildingError, AttributeInferenceError, InferenceError):
            return ()
    return tuple(members)


def _module_attribute(node: astroid.Attribute, ctx: Context) -> ProxySort:
    members = _module_members(node)
    if not members:
        raise UnsupportedError("cannot resolve module attribute", node.as_string())
    if not all(
        _is_static_module_member(member) for member in members
    ):
        raise UnsupportedError("mutable or non-literal module attribute", node.as_string())
    definitions = _infer_static(node)
    if not definitions or any(
        not isinstance(target, (astroid.NodeNG, astroid.UnboundMethod))
        for target in definitions
    ):
        raise UnsupportedError("cannot resolve module attribute", node.as_string())
    target = definitions[0]
    if all(
        isinstance(definition, (astroid.FunctionDef, astroid.UnboundMethod))
        for definition in definitions
    ):
        functions = [
            FUNCTIONS.get(".".join(get_full_name(definition)))
            for definition in definitions
        ]
        if functions[0] is not None and all(
            function is functions[0] for function in functions
        ):
            # Different inferred definitions may share a model only when its
            # semantics are identical for every alternative. Platform-specific
            # path joins have separate models and remain ambiguous here.
            return FuncSort(functions[0])
        raise UnsupportedError("no unambiguous definition for", node.as_string())
    if not all(definition is target for definition in definitions):
        if not all(
            isinstance(definition, astroid.Const)
            and isinstance(target, astroid.Const)
            and type(definition.value) is type(target.value)
            and repr(definition.value) == repr(target.value)
            for definition in definitions
        ):
            raise UnsupportedError("ambiguous module attribute", node.as_string())
    if not _is_immutable_module_value(target):
        # Inference can preserve a container's initial literal despite later
        # mutation. Computed elements can also capture the caller's scope or
        # discard module initialization.
        raise UnsupportedError("mutable or non-literal module attribute", node.as_string())
    # An immutable value does not make its module binding immutable. A trusted
    # initializer or a later call can replace it while inference still sees
    # this declaration. A live snapshot would have the same lifetime problem.
    raise UnsupportedError("module data bindings are not modeled", node.as_string())


def _is_static_module_member(node: astroid.NodeNG) -> bool:
    if isinstance(node, (astroid.Const, astroid.FunctionDef, astroid.UnboundMethod)):
        return True
    if isinstance(node, astroid.AssignName) and isinstance(
        node.parent, (astroid.Assign, astroid.AnnAssign)
    ):
        return _is_immutable_module_value(node.parent.value)
    return False


def _is_immutable_module_value(node: astroid.NodeNG | None) -> bool:
    """Classify literal declarations without granting their bindings stability."""
    if isinstance(node, astroid.Const):
        return True
    if isinstance(node, astroid.Tuple):
        return all(_is_immutable_module_value(element) for element in node.elts)
    if isinstance(node, astroid.UnaryOp) and node.op in {"+", "-"}:
        return _is_immutable_module_value(node.operand)
    return False


@eval_expr.register(astroid.Attribute)
def eval_attr(node: astroid.Attribute, ctx: Context) -> ProxySort:
    if _is_module_reference(node.expr, ctx):
        return _module_attribute(node, ctx)
    # Inference is safe only for the explicit module paths above. It must never
    # replace a failed receiver evaluation: a callee may have effects or proof
    # obligations that have not reached the caller, and an invented return
    # value can make a live branch appear unreachable. All refusals propagate,
    # including body execution, trusted return materialization, and row errors.
    expr_ref = eval_expr(node=node.expr, ctx=ctx)
    if isinstance(expr_ref, NoneSort):
        raise UnsupportedError('unsupported operation on None', node.attrname)
    _require_attribute_surface(expr_ref, node.attrname)
    value = expr_ref.m_getattr(node.attrname, ctx=ctx)
    if isinstance(value, Method):
        direct_call = isinstance(node.parent, astroid.Call) and node.parent.func is node
        if not value.pure:
            if not direct_call or not isinstance(node.expr, astroid.Name):
                # Dispatch writes back to the source name; a saved method or
                # computed receiver has no corresponding storage location.
                raise UnsupportedError(
                    'mutating method requires a direct call on a named receiver',
                    node.attrname,
                )
            from ._mutation_bindings import require_local_mutation_receiver

            require_local_mutation_receiver(node.expr)
        elif not direct_call and not _immutable_method_receiver(expr_ref):
            # A saved Method holds a snapshot proxy, whereas Python retains
            # the live collection. Even a pure query can observe later writes.
            raise UnsupportedError(
                'methods on mutable receivers require an immediate call',
                node.attrname,
            )
    return value


def _immutable_method_receiver(value: ProxySort) -> bool:
    if isinstance(value, (types.list, types.dict, types.set)):
        return False
    if isinstance(value, (types.str, types.int, types.float, types.bool)):
        return True
    if isinstance(value, types.tuple):
        return all(
            _immutable_method_receiver(subtype.default)
            for subtype in value.factory.subtypes
        )
    return False


ATTRIBUTE_RECEIVER_PAIRS = (
    (types.str, str), (types.list, list), (types.tuple, tuple),
    (types.set, set), (types.dict, dict), (types.int, int),
    (types.float, float),
)


def _require_attribute_surface(value: ProxySort, name: str) -> None:
    # Source extraction must be executable even when the resulting method is
    # only saved, never called. Internal operator dispatch still uses the
    # proxy's full table; public attributes share the interpreter's surface.
    from clauz3.interp.methods import MODELLED_METHODS
    from ._proxies._row import RowSort

    if isinstance(value, RowSort) and name in value.schema.__annotations__:
        return
    for proxy, runtime in ATTRIBUTE_RECEIVER_PAIRS:
        if isinstance(value, proxy):
            if name in MODELLED_METHODS.get(runtime, frozenset()):
                return
            # ListSort inherits the tuple proxy; only the first, most
            # specific receiver table may authorize a source attribute.
            break
    if name == '__call__':
        raise UnsupportedError(
            'explicit __call__ attributes are not supported; call the function directly',
        )
    raise UnsupportedError(
        'attribute is outside the interpreter surface',
        getattr(value, 'type_name', type(value).__name__), name,
    )


@eval_expr.register(astroid.UnaryOp)
def eval_unary_op(node: astroid.UnaryOp, ctx: Context) -> ProxySort:
    value_ref = eval_expr(node=node.operand, ctx=ctx)
    if node.op == "+":
        return value_ref.m_pos(ctx=ctx)
    if node.op == "-":
        return value_ref.m_neg(ctx=ctx)
    if node.op == "~":
        return value_ref.m_inv(ctx=ctx)
    if node.op == "not":
        return value_ref.m_not(ctx=ctx)
    raise RuntimeError("unreachable")


@eval_expr.register(astroid.IfExp)
def eval_ternary_op(node: astroid.IfExp, ctx: Context) -> ProxySort:
    assert node.test is not None
    assert node.body is not None
    assert node.orelse is not None

    condition = eval_expr(node=node.test, ctx=ctx).m_bool(ctx=ctx)
    known = z3.simplify(condition.expr)
    live = ctx.interrupted.m_not(ctx=ctx)
    if z3.is_true(known) or z3.is_false(known):
        selected = node.body if z3.is_true(known) else node.orelse
        return _eval_ternary_arm(node=selected, guard=live, ctx=ctx)

    # Capture both guards before either branch contributes its exceptions.
    # One arm cannot suppress obligations or facts belonging to the other.
    then_guard = and_expr(live, condition, ctx=ctx)
    else_guard = and_expr(live, condition.m_not(ctx=ctx), ctx=ctx)
    then_ref = _eval_ternary_arm(node=node.body, guard=then_guard, ctx=ctx)
    else_ref = _eval_ternary_arm(node=node.orelse, guard=else_guard, ctx=ctx)
    if type(then_ref) is not type(else_ref):
        raise UnsupportedError(
            'cannot merge conditional expression arms of different types',
            then_ref.type_name, else_ref.type_name,
        )
    return if_expr(condition, then_ref, else_ref, ctx=ctx)


def _eval_ternary_arm(*, node: astroid.NodeNG, guard: ProxySort, ctx: Context) -> ProxySort:
    branch = ctx.make_child()
    with ctx.trace.guard(GUARDED_EXPRESSION):
        value = eval_expr(node=node, ctx=branch)
    _require_unchanged_expression_scope(branch)
    if not isinstance(value, (types.bool, types.int, types.float, types.str, NoneSort)):
        raise UnsupportedError(
            'unsupported conditional expression result type',
            getattr(value, 'type_name', type(value).__name__),
        )
    merge_branch_layers(branch=branch, guard=guard.m_bool(ctx=ctx), ctx=ctx)
    return value


@eval_expr.register(astroid.Call)
def eval_call(node: astroid.Call, ctx: Context) -> ProxySort:
    # User arguments share a Python dispatch call with internal metadata.
    # Refuse collisions before **kwargs expansion can raise a raw TypeError.
    # Validate duplicates before constructing a dict, which would discard them.
    keyword_names: set[str] = set()
    for keyword in node.keywords or ():
        if keyword.arg is None:
            raise UnsupportedError(
                "unsupported call argument binding: keyword unpacking"
            )
        if keyword.arg in {"self", "ctx", "var_name"}:
            raise UnsupportedError(
                "unsupported call argument binding: reserved keyword", keyword.arg
            )
        if keyword.arg in keyword_names:
            raise UnsupportedError(
                "unsupported call argument binding: duplicate keyword", keyword.arg
            )
        keyword_names.add(keyword.arg)
    if any(isinstance(arg, astroid.Starred) for arg in node.args):
        raise UnsupportedError(
            "unsupported call argument binding: positional unpacking"
        )
    from clauz3.contract_literals import materialize_contract_argument
    from clauz3.spec import _ContractSolver
    from ._proxies._row import RowClassSort

    # Python resolves the callable before its arguments. Registered contract
    # builders need their exact source values, before Z3 encoding can lose
    # signed zero, string escapes, container shape, or Python truthiness.
    value = eval_expr(node=node.func, ctx=ctx)
    implementation = value.impl if isinstance(value, FuncSort) else None
    contract = isinstance(implementation, _ContractSolver) or isinstance(
        getattr(implementation, 'impl', None), _ContractSolver,
    )

    def resolve_row(name):
        row = eval_expr(node=name, ctx=ctx)
        if not isinstance(row, RowClassSort):
            raise UnsupportedError(
                'contract helper arguments must be source literals or Row classes',
                name.as_string(),
            )
        return row.schema

    def argument(arg_node):
        if contract:
            return materialize_contract_argument(arg_node, resolve_row=resolve_row)
        return eval_expr(node=arg_node, ctx=ctx)

    call_args = []
    for arg_node in node.args:
        call_args.append(argument(arg_node))

    call_kwargs = {}
    if node.keywords:
        for keyword in node.keywords:
            assert isinstance(keyword, astroid.Keyword)
            assert keyword.arg is not None
            call_kwargs[keyword.arg] = argument(keyword.value)

    target_name = node.func.as_string()
    # Resolve the callable's binding before selecting a model. Source text
    # such as ``math.sin`` can instead name an aliased trusted effect.
    _require_int_call_shape(value=value, node=node)
    if call_kwargs:
        # Refuse lambda keywords before adding dispatch metadata: user names
        # such as ctx/var_name must not collide with internal keyword arguments.
        target = value
        while isinstance(target, Method) and target.name == '__call__':
            target = target.obj
        if isinstance(target, LambdaSort):
            raise UnsupportedError('unsupported lambda argument binding')

    method = value
    while isinstance(method, Method) and method.name == '__call__' and method.obj is not None:
        method = method.obj
    if (
        node.keywords
        and isinstance(method, Method)
        and method.name in {'index', 'find'}
        and isinstance(method.obj, types.str)
    ):
        # String search methods accept positional arguments only. Refuse before injecting
        # evaluator metadata, including through a saved bound-method alias.
        raise UnsupportedError(f'unsupported str.{method.name} argument binding')
    _require_count_call_shape(value=value, node=node)
    if isinstance(node.func, astroid.Attribute):
        var_name = node.func.expr.as_string()
    else:
        var_name = target_name
    if not proof_monitor.active():
        return value.m_call(*call_args, ctx=ctx, var_name=var_name, **call_kwargs)
    with proof_monitor.call_site(node):
        return value.m_call(*call_args, ctx=ctx, var_name=var_name, **call_kwargs)


def _require_count_call_shape(*, value: ProxySort, node: astroid.Call) -> None:
    target = value
    seen: set[int] = set()
    while isinstance(target, Method) and target.name == '__call__':
        if id(target) in seen or target.obj is None:
            raise UnsupportedError('unsupported callable wrapper')
        seen.add(id(target))
        target = target.obj
    if not (isinstance(target, Method) and target.name == 'count'
            and isinstance(target.obj, types.str)):
        return
    if len(node.args) != 1 or node.keywords or isinstance(node.args[0], astroid.Starred):
        raise UnsupportedError('str.count() requires one positional substring; start/end are not supported')


def _require_int_call_shape(*, value: ProxySort, node: astroid.Call) -> None:
    # Check the resolved implementation, including saved __call__ wrappers,
    # before injecting internal ctx/var_name keywords. A local function named
    # int has its own signature and must not acquire builtin authority.
    target = value
    seen: set[int] = set()
    while isinstance(target, Method) and target.name == '__call__':
        if id(target) in seen or target.obj is None:
            raise UnsupportedError('unsupported callable wrapper')
        seen.add(id(target))
        target = target.obj
    if not isinstance(target, FuncSort) or target.impl is not FUNCTIONS['builtins.int']:
        return
    if len(node.args) != 1 or node.keywords or isinstance(node.args[0], astroid.Starred):
        raise UnsupportedError('int() requires one positional argument and no keywords')


@eval_expr.register(astroid.Lambda)
def eval_lambda(node: astroid.Lambda, ctx: Context) -> LambdaSort:
    from ._ownership import capture_names

    capture_names(node, ctx=ctx)
    return LambdaSort(
        ctx=ctx,
        args=node.args,
        body=node.body,
    )
