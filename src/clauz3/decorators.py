"""Resolve a decorator to the object it names, not to how it was spelled.

Three consumers have to agree about which decorators on an agent-authored
function are ``@clauz3.guarantee``:

* the prover (:meth:`clauz3.prover.AgentTheorem.get_guarantees`) turns them into
  proof obligations,
* the approval request (:func:`clauz3.runner._extract_guarantees`) shows them to
  the human who consents to the run, and
* the interpreter (:func:`clauz3.interp.interpreter._check_decorators`) refuses
  anything outside the proved subset.

Each of them used to carry its own copy of the literal source text
``{"clauz3.guarantee", "guarantee"}``. That is not a test of what the decorator
*is*: ``import clauz3 as c`` spells the same object ``c.guarantee`` and was
dropped by the first two and refused by the third, while ``import json as
clauz3`` spells a different object ``clauz3.guarantee`` and was accepted by all
three (issue #129).

This module resolves the head of a dotted decorator through the module's import
bindings instead, which is exactly the mechanism
:func:`clauz3.prover.validate_untrusted_source` already trusts for the
security-critical direction — keeping effect decorators out of untrusted
source. The
guarantee gate must not use a weaker one.

The map is built from ``import`` statements alone, and a head it cannot resolve
is returned unchanged. That is deliberate rather than lossy: the interpreter
refuses ``from X import *`` and non-aliased dotted ``import a.b.c``, and a
module-level rebinding (``g = clauz3.guarantee``) is not a constant expression,
so no other binding form survives to the executor. Whatever the map does not
recognise therefore does not resolve to ``clauz3.guarantee``, and the callers
refuse it rather than ignore it.
"""

import ast
from collections.abc import Iterator, Mapping
from typing import Any, TypeAlias

import astroid

#: The one decorator agent-authored source may carry.
GUARANTEE = "clauz3.guarantee"

#: A fluent update registered when the trusted layer is imported.
FLUENT_EFFECT = "clauz3.fluent.effect"

Module: TypeAlias = ast.Module | astroid.Module
DecoratorNode: TypeAlias = ast.expr | astroid.NodeNG


def import_aliases(module: Module) -> dict[str, str]:
    """Map each locally bound name to the dotted target it names.

    ``import clauz3 as c`` -> ``{"c": "clauz3"}``; ``from clauz3 import
    guarantee as g`` -> ``{"g": "clauz3.guarantee"}``; ``import deal`` ->
    ``{"deal": "deal"}``.

    Accepts either an :mod:`ast` or an :mod:`astroid` module, because the prover
    walks astroid and the approval request walks ast. Deriving both from one
    function is the point: two alias maps could disagree the same way the two
    literal sets did.

    >>> import ast
    >>> import_aliases(ast.parse("import clauz3 as c"))
    {'c': 'clauz3'}
    >>> import_aliases(ast.parse("from clauz3 import guarantee as g"))
    {'g': 'clauz3.guarantee'}
    """
    aliases: dict[str, str] = {}
    for modname, names in _imports(module):
        for name, asname in names:
            if modname is None:
                # ``import X`` binds ``X``; ``import X.Y`` binds ``X``.
                aliases[asname] = name if asname else name.split(".", 1)[0]
            else:
                aliases[asname or name] = f"{modname}.{name}"
    return aliases


def _imports(module: Module) -> Iterator[tuple[str | None, list[tuple[str, str]]]]:
    """Yield ``(module or None for plain import, [(name, asname)])`` per import.

    ``asname`` is normalised to ``""`` when absent, matching astroid, so the two
    parser front ends produce identical alias maps.
    """
    for node in _module_scope(module):
        if isinstance(node, ast.Import):
            yield None, [(alias.name, alias.asname or "") for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield (
                node.module,
                [(alias.name, alias.asname or "") for alias in node.names],
            )
        elif isinstance(node, astroid.Import):
            yield None, [(name, asname or "") for name, asname in node.names]
        elif isinstance(node, astroid.ImportFrom) and node.modname and not node.level:
            yield node.modname, [(name, asname or "") for name, asname in node.names]


#: Statements that introduce a new name scope. An import inside one binds a
#: local, not a module attribute, so it cannot be what a module-level decorator
#: resolves through.
_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    astroid.FunctionDef,
    astroid.AsyncFunctionDef,
    astroid.ClassDef,
    astroid.Lambda,
)


def _module_scope(node: Module | ast.AST | astroid.NodeNG) -> Iterator[Any]:
    """Every statement in the module's own scope, not its functions' or classes'.

    Collecting imports with a plain walk read *local* imports as though they
    bound module names, and the last one won. A trusted layer is ordinary
    Python, so::

        import clauz3 as c

        @c.trusted
        def send_email(addr: str, msg: str) -> None: ...

        def unused_helper() -> None:
            import calendar as c

    made ``c`` resolve to ``calendar``, the boundary go unrecognised, the body
    be inlined, and a guarantee over its facts hold vacuously --- for a helper
    that is never called. Nested control flow (``if``, ``try``, ``with``) does
    not open a scope, so its imports still count.
    """
    children = (
        ast.iter_child_nodes(node) if isinstance(node, ast.AST) else node.get_children()
    )
    for child in children:
        if isinstance(child, _SCOPES):
            continue
        yield child
        yield from _module_scope(child)


def canonical_target(dotted: str, aliases: Mapping[str, str]) -> str:
    """Rewrite a dotted decorator spelling to the target it names.

    An unbound head is returned unchanged, so a fully qualified
    ``clauz3.guarantee`` still resolves in the rewritten sources that
    :func:`clauz3.prover._inject_required_guarantees` and
    :func:`clauz3.approvals.policy._conjoin` synthesise.

    >>> canonical_target("c.guarantee", {"c": "clauz3"})
    'clauz3.guarantee'
    >>> canonical_target("clauz3.guarantee", {"clauz3": "json"})
    'json.guarantee'
    >>> canonical_target("clauz3.guarantee", {})
    'clauz3.guarantee'
    """
    head, dot, rest = dotted.partition(".")
    base = aliases.get(head)
    if base is None:
        return dotted
    return f"{base}.{rest}" if dot else base


def dotted_name(node: DecoratorNode) -> str:
    """The dotted source spelling of a decorator, with any call peeled off.

    ``@c.guarantee(expr)`` -> ``"c.guarantee"``. Returns ``""`` for a decorator
    that is not a plain dotted name, such as ``@(lambda f: f)``; callers treat
    that as unresolvable, which is a refusal.

    >>> import ast
    >>> tree = ast.parse("@c.guarantee(1)\\ndef f(): ...")
    >>> dotted_name(tree.body[0].decorator_list[0])
    'c.guarantee'
    """
    if isinstance(node, ast.Call):
        return dotted_name(node.func)
    if isinstance(node, astroid.Call):
        return dotted_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, astroid.Name):
        return str(node.name)
    # ``ast`` and ``astroid`` spell attribute access differently: ``value``/
    # ``attr`` against ``expr``/``attrname``.
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, astroid.Attribute):
        base = dotted_name(node.expr)
        attr = str(node.attrname)
        return f"{base}.{attr}" if base else attr
    return ""


def is_guarantee(node: DecoratorNode, aliases: Mapping[str, str]) -> bool:
    """True iff this decorator names :func:`clauz3.guarantee`.

    >>> import ast
    >>> tree = ast.parse("import clauz3 as c\\n@c.guarantee(1)\\ndef f(): ...")
    >>> is_guarantee(tree.body[1].decorator_list[0], import_aliases(tree))
    True
    >>> tree = ast.parse("import json as clauz3\\n@clauz3.guarantee(1)\\ndef f(): ...")
    >>> is_guarantee(tree.body[1].decorator_list[0], import_aliases(tree))
    False
    """
    return canonical_target(dotted_name(node), aliases) == GUARANTEE


#: The decorator a trusted root uses to declare an effect boundary, by every
#: target it can canonically resolve to: ``clauz3.trusted`` is the re-export,
#: ``clauz3.effects.trusted`` the definition.
TRUSTED = frozenset({"clauz3.trusted", "clauz3.effects.trusted"})


def is_mock(node: DecoratorNode, aliases: Mapping[str, str]) -> bool:
    """Recognize an explicit mock declaration; this never grants authority.

    Accept the bare decorator and its empty-call spelling, including aliases
    and declarations unfolded from ``deal.chain`` by the caller.
    """
    if canonical_target(dotted_name(node), aliases) not in {
        "clauz3.mock",
        "clauz3.effects.mock",
    }:
        return False
    if isinstance(node, ast.Call | astroid.Call):
        return not node.args and not node.keywords
    return True


#: The pre-clauz3 spelling, still honoured so an existing trusted layer keeps
#: proving. Unlike deal's own extractor, which matches the literal text
#: ``deal.has``, this is resolved through the import bindings, so ``import deal
#: as d; @d.has(...)`` is a boundary here rather than a silently inlined
#: function. See :mod:`clauz3.effects`.
DEAL_HAS = "deal.has"


def is_trusted(node: DecoratorNode, aliases: Mapping[str, str]) -> bool:
    """True iff this decorator names :func:`clauz3.trusted`.

    >>> import ast
    >>> tree = ast.parse("import clauz3\\n@clauz3.trusted\\ndef f(): ...")
    >>> is_trusted(tree.body[1].decorator_list[0], import_aliases(tree))
    True
    >>> src = "import json as clauz3\\n@clauz3.trusted\\ndef f(): ..."
    >>> tree = ast.parse(src)
    >>> is_trusted(tree.body[1].decorator_list[0], import_aliases(tree))
    False
    """
    return canonical_target(dotted_name(node), aliases) in TRUSTED


#: The conditions a trusted layer states about a function's values, by every
#: target each can canonically resolve to. They carry deal's contract names
#: because that is what the symbolic executor evaluates them as.
REQUIRES = frozenset({"clauz3.requires", "clauz3.conditions.requires"})
ENSURES = frozenset({"clauz3.ensures", "clauz3.conditions.ensures"})

#: deal's own contract decorators, which the prover reads too so a layer
#: written before :mod:`clauz3.conditions` keeps proving. Note that deal's
#: extractor matches only the literal text ``deal.pre`` and friends, while
#: these are resolved through the import bindings -- which is the difference
#: between an aliased ``@d.pre`` being an obligation and being silently
#: dropped.
DEAL_CONTRACTS = frozenset({"pre", "post", "ensure", "raises", "has"})


def condition_contract(node: DecoratorNode, aliases: Mapping[str, str]) -> str | None:
    """The contract name this decorator states, or ``None``.

    ``@clauz3.requires(...)`` -> ``"pre"``, and an alias-resolved
    ``@deal.pre(...)`` -> ``"pre"``.

    ``@clauz3.ensures(...)`` is one decorator over two of deal's, chosen by the
    predicate's arity, because the two bind their lambda differently:
    ``post`` binds the single parameter to the return value positionally, so it
    may be called anything, while ``ensure`` binds the name ``result`` and
    leaves the function's own parameters in scope -- which is what lets a
    postcondition say something about the result *in terms of the arguments*.

    >>> import ast
    >>> src = "import clauz3\\n@clauz3.ensures(lambda r: r)\\ndef f(): ..."
    >>> tree = ast.parse(src)
    >>> condition_contract(tree.body[1].decorator_list[0], import_aliases(tree))
    'post'
    >>> body = "@clauz3.ensures(lambda cap, result: result)\\ndef f(): ..."
    >>> src = "import clauz3\\n" + body
    >>> tree = ast.parse(src)
    >>> condition_contract(tree.body[1].decorator_list[0], import_aliases(tree))
    'ensure'
    >>> src = "import deal as d\\n@d.post(lambda r: r)\\ndef f(): ..."
    >>> tree = ast.parse(src)
    >>> condition_contract(tree.body[1].decorator_list[0], import_aliases(tree))
    'post'
    """
    canonical = canonical_target(dotted_name(node), aliases)
    if canonical in REQUIRES:
        return "pre"
    if canonical in ENSURES:
        return "post" if _predicate_arity(node) == 1 else "ensure"
    head, dot, rest = canonical.partition(".")
    if head == "deal" and dot and rest in DEAL_CONTRACTS:
        return rest
    return None


#: How a condition decorator's predicate is spelled when it is passed by
#: keyword: ``predicate`` by :mod:`clauz3.conditions`, ``validator`` by deal's
#: own contracts. Both are ordinary Python callables, so a trusted layer may
#: write either, and the prover has to read the predicate wherever it is.
PREDICATE_KEYWORDS = frozenset({"predicate", "validator"})


def predicate_argument(node: DecoratorNode) -> DecoratorNode | None:
    """The predicate this condition decorator was called with, or ``None``.

    >>> import ast
    >>> decorated = "@clauz3.requires(predicate=lambda a: a)"
    >>> src = "import clauz3\\n" + decorated + "\\ndef f(): ..."
    >>> tree = ast.parse(src)
    >>> node = predicate_argument(tree.body[1].decorator_list[0])
    >>> ast.unparse(node)
    'lambda a: a'
    """
    args = getattr(node, "args", None) or []
    if args:
        first: DecoratorNode = args[0]
        return first
    for keyword in getattr(node, "keywords", None) or []:
        if getattr(keyword, "arg", None) in PREDICATE_KEYWORDS:
            value: DecoratorNode = keyword.value
            return value
    return None


def _predicate_arity(node: DecoratorNode) -> int:
    """How many positional parameters this decorator's lambda declares.

    Positional, because that is how both of deal's postconditions call it:
    ``post`` passes the return value, ``ensure`` passes the arguments by name
    plus ``result``. A positional-only parameter counts; a keyword-only one
    could never be bound by either, so it does not.

    Zero when there is no lambda to read, which leaves ``@clauz3.ensures`` as a
    ``post`` -- the evaluator ignores a non-lambda contract either way.

    The predicate may be passed by keyword, and that is not a curiosity here:
    ``post`` binds its lambda's single parameter to the return value while
    ``ensure`` binds the name ``result``, so reading the arity of a
    ``predicate=`` lambda as zero would state a two-parameter postcondition as
    the wrong one of deal's contracts.
    """
    predicate = predicate_argument(node)
    if isinstance(predicate, ast.Lambda):
        return len(predicate.args.posonlyargs) + len(predicate.args.args)
    if isinstance(predicate, astroid.Lambda):
        posonly = predicate.args.posonlyargs or []
        return len(posonly) + len(predicate.args.args or [])
    return 0


def is_deal_has(node: DecoratorNode, aliases: Mapping[str, str]) -> bool:
    """True iff this decorator names :func:`deal.has`, however it is spelled.

    >>> import ast
    >>> tree = ast.parse("import deal as d\\n@d.has('io')\\ndef f(): ...")
    >>> is_deal_has(tree.body[1].decorator_list[0], import_aliases(tree))
    True
    """
    return canonical_target(dotted_name(node), aliases) == DEAL_HAS


def deal_markers(node: DecoratorNode) -> tuple[str, ...]:
    """The string-literal markers a legacy ``@deal.has(...)`` carries.

    They are deal's, not clauz3's: what they decide here is only whether the
    legacy spelling makes a boundary at all (deal reads a bare ``@deal.has()``
    as "no side effects", so it must not).

    >>> import ast
    >>> tree = ast.parse("@deal.has('read', 'io')\\ndef f(): ...")
    >>> deal_markers(tree.body[0].decorator_list[0])
    ('read', 'io')
    """
    if isinstance(node, ast.Call):
        return tuple(
            arg.value
            for arg in node.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        )
    if isinstance(node, astroid.Call):
        return tuple(
            str(arg.value)
            for arg in node.args
            if isinstance(arg, astroid.Const) and isinstance(arg.value, str)
        )
    return ()


def is_trusted_effect(
    node: ast.FunctionDef | astroid.FunctionDef,
    aliases: Mapping[str, str],
) -> bool:
    """Whether this function is a trusted effect: the boundary the prover skips.

    ``@clauz3.trusted`` says so by being there. The legacy ``@deal.has(...)``
    says so only when it carries a marker, which is deal's own meaning --- and
    it counts wherever it is written, including inside a ``@deal.chain(...)``
    bundle, which is how deal lets a layer state several contracts at once.
    """
    for decorator in _decorator_nodes(node):
        for declared in unfold_chain(decorator, aliases):
            if is_trusted(declared, aliases):
                return True
            if is_deal_has(declared, aliases) and deal_markers(declared):
                return True
    return False


def unfold_chain(
    node: DecoratorNode,
    aliases: Mapping[str, str],
) -> Iterator[DecoratorNode]:
    """This decorator, or the contracts it bundles if it is ``deal.chain``.

    >>> import ast
    >>> src = "import deal as d\\n@d.chain(d.has('io'))\\ndef f(): ..."
    >>> tree = ast.parse(src)
    >>> chained = unfold_chain(
    ...     tree.body[1].decorator_list[0], import_aliases(tree)
    ... )
    >>> [dotted_name(node) for node in chained]
    ['d.has']
    """
    if canonical_target(dotted_name(node), aliases) != "deal.chain":
        yield node
        return
    for argument in getattr(node, "args", []):
        yield from unfold_chain(argument, aliases)


def _decorator_nodes(
    node: ast.FunctionDef | astroid.FunctionDef,
) -> list[DecoratorNode]:
    if isinstance(node, ast.FunctionDef):
        return list(node.decorator_list)
    decorators = node.decorators
    return list(decorators.nodes) if decorators is not None else []
