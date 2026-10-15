import ast
import importlib
import inspect
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import astroid
import deal.introspection
import z3
from deal.linter._extractors import get_contracts

from clauz3 import decorators as decorators_mod
from clauz3 import interp, spec
from clauz3._vendor import deal_solver
from clauz3._vendor.deal_solver._context import FactInfo
from clauz3._vendor.deal_solver._proxies import ProxySort
from clauz3._vendor.deal_solver._proxies._row import row_schema_from_class_def
from clauz3._vendor.deal_solver._types import AstNode
from clauz3.diagnostics import proof as proof_monitor
from clauz3.entry_bindings import target_binding_error
from clauz3.guarantees import (
    GuaranteeError,
    guarantee_expressions,
    validate_guarantees,
)
from clauz3.imports_policy import (
    DisallowedImportError,
    build_policy,
    validate_imports,
)
from clauz3.interp.library_origins import validate_trusted_module
from clauz3.layer_state import LayerImage, LayerStateError, selected_layer
from clauz3.libraries.layout import LayoutError, expand_import_roots
from clauz3.policy import (
    DomainPolicy,
    DuplicateEffectError,
    domain_triggered,
    load_domain_policies,
    load_effect_markers,
    load_trusted_effects,
    transitive_calls,
)
from clauz3.row import RowSchemaError
from clauz3.spec import REGISTERED_CONTRACTS


@dataclass(frozen=True)
class ProofObservation:
    """The proved model retained for independent concrete execution checks.

    ``assumptions`` is the frozen antecedent used by the proof, excluding the
    guarantee itself. Each effect retains its path condition, loop binders,
    arguments, and materialized trusted return value. ``contracts`` contains
    declarative specifications reconstructed with the original Python literal
    arguments, including required domain policies. They deliberately bypass
    symbolic argument conversion so its errors remain detectable.
    """

    assumptions: z3.BoolRef
    arguments: dict[str, ProxySort]
    effects: tuple[FactInfo, ...]
    contracts: tuple[spec.ContractSpec, ...]
    guarantees: tuple[str, ...]


@dataclass(frozen=True)
class ProofResult:
    name: str
    proof: deal_solver.Proof
    observation: ProofObservation | None = None

    @property
    def ok(self) -> bool:
        return self.proof.conclusion == deal_solver.Conclusion.OK


class ProverConfigError(Exception):
    """The requested proof configuration is not valid."""


class AgentTheorem(deal_solver.Theorem):
    def observation(self, *, source: str, source_name: str) -> ProofObservation:
        """Snapshot a successful theorem while its trusted layer is selected."""
        return ProofObservation(
            assumptions=self.assumptions.expr,
            arguments=dict(self.arguments),
            effects=tuple(self._context.facts),
            contracts=_concrete_contracts(
                source=source, source_name=source_name, target=self.name
            ),
            guarantees=tuple(
                node.as_string() for node in self.get_guarantees(self._func)
            ),
        )

    @staticmethod
    def get_contracts(func: astroid.FunctionDef) -> Iterator[deal_solver.Contract]:
        for contract in get_contracts(func):
            if contract.name in _PREDICATE_CONTRACTS:
                if not contract.args:
                    # deal's extractor reads a predicate positionally, so
                    # ``@deal.pre(validator=lambda ...)`` reaches it as a
                    # contract with no arguments -- which the evaluator indexes
                    # into. Dropped here and re-read from the decorator below.
                    continue
                _require_lambda(contract.args[0], f"@deal.{contract.name}")
            yield deal_solver.Contract(
                name=contract.name,
                args=cast(list[AstNode], contract.args),
            )
        yield from _clauz3_contracts(func)

    @staticmethod
    def get_guarantees(func: astroid.FunctionDef) -> Iterator[astroid.NodeNG]:
        decorators = func.decorators
        if decorators is None:
            return
        aliases = decorators_mod.import_aliases(func.root())
        for decorator in decorators.nodes:
            if not isinstance(decorator, astroid.Call):
                continue
            if not decorators_mod.is_guarantee(decorator, aliases):
                continue
            if not decorator.args:
                continue
            yield decorator.args[0]


def _clauz3_contracts(func: astroid.FunctionDef) -> Iterator[deal_solver.Contract]:
    """Yield the declarations deal's own extractor cannot see.

    deal's extractor (``deal/linter/_extractors/contracts.py``) matches the
    literal dotted text ``deal.pre``, ``deal.has`` and friends. Everything
    resolved rather than spelled has to be found here:

    ``effect``
        ``@clauz3.trusted``, which takes no arguments: its presence is the
        whole declaration, and the symbolic executor records every call to the
        function it decorates as a fact.

    ``pre`` / ``post``
        ``@clauz3.requires(...)`` / ``@clauz3.ensures(...)``, and the same
        contracts written under a deal import alias (``import deal as d``).
        The alias case is not a cosmetic miss: a dropped ``pre`` is a dropped
        proof obligation, so a call the precondition forbids proves clean.

    ``ensure`` / ``raises`` / ``has``
        deal's remaining contracts, likewise rescued when an alias hides them
        from deal's extractor. ``has`` keeps deal's meaning -- a marker makes
        the boundary, and a bare ``@deal.has()`` asserts purity.
    """
    aliases = decorators_mod.import_aliases(func.root())
    decorator_nodes = func.decorators.nodes if func.decorators is not None else []
    for decorator in decorator_nodes:
        yield from _declared_contracts(decorator, aliases, seen_by_deal=True)


#: The contracts whose one argument is the predicate the solver evaluates. The
#: others (``raises`` over exception classes, ``has`` over markers) take any
#: number, including none.
_PREDICATE_CONTRACTS = frozenset({"pre", "post", "ensure"})


def _unsupported_decorator(node: astroid.NodeNG) -> deal_solver.UnsupportedError:
    return deal_solver.UnsupportedError(
        f"unsupported decorator @{node.as_string()}: the prover cannot resolve "
        "its declarations and will not assume the function is undecorated. "
        "Use a direct import of a supported clauz3 or deal decorator "
        "(import aliases are supported); re-exports, assignment aliases, and "
        "custom decorators are not supported.",
    )


def _check_decorator_binding(node: astroid.NodeNG, aliases: Mapping[str, str]) -> None:
    """Refuse rebinding that an import-only alias map cannot represent.

    In particular, importing ``guarantee as boundary`` and then assigning
    ``boundary = clauz3.trusted`` must not hide a boundary behind a decorator
    we otherwise allow. Require every binding returned by astroid to agree
    with the module-wide alias map, including across later imports.
    """
    head = node.func if isinstance(node, astroid.Call) else node
    while isinstance(head, astroid.Attribute):
        head = head.expr
    if not isinstance(head, astroid.Name):
        raise _unsupported_decorator(node)
    _, bindings = head.lookup(head.name)
    if not bindings:
        raise _unsupported_decorator(node)
    for binding in bindings:
        if isinstance(binding, astroid.Import):
            at_declaration = {
                asname or name.split(".", 1)[0]: name
                if asname
                else name.split(".", 1)[0]
                for name, asname in binding.names
            }
        elif isinstance(binding, astroid.ImportFrom) and not binding.level:
            at_declaration = {
                asname or name: f"{binding.modname}.{name}"
                for name, asname in binding.names
            }
        else:
            raise _unsupported_decorator(node)
        if at_declaration.get(head.name) != aliases.get(head.name, head.name):
            raise _unsupported_decorator(node)


def _declared_contracts(
    node: astroid.NodeNG,
    aliases: dict[str, str],
    *,
    seen_by_deal: bool,
) -> Iterator[deal_solver.Contract]:
    """The contracts one decorator declares, unfolding ``deal.chain``.

    ``@deal.chain(deal.has("io"), deal.pre(...))`` bundles several contracts in
    one decorator, and deal's extractor unfolds it -- by literal text, at every
    level. So ``import deal as d`` hides the whole bundle: neither the boundary
    nor the precondition is read, and a call the precondition forbids proves
    clean, exactly as an aliased ``@d.pre`` did.

    ``seen_by_deal`` tracks whether deal's extractor reached this node, so a
    contract it already yielded is not counted twice. It stops being true as
    soon as one level is spelled through an alias, because deal's recursion
    stops there too.
    """
    _check_decorator_binding(node, aliases)
    if decorators_mod.is_mock(node, aliases):
        # Descriptive, identity-preserving metadata. It supplies neither an
        # effect boundary nor any assumptions about the implementation.
        return
    if decorators_mod.is_trusted(node, aliases):
        # Tested here rather than in the loop above so a native boundary counts
        # wherever it is written, including inside a bundle:
        # ``@deal.chain(clauz3.trusted, deal.pre(...))``. The policy scanner
        # unfolds chains, so testing only the top level made the two disagree
        # in the fail-open direction -- the scanner listed the effect (which is
        # what a relation's name is checked against) while the prover inlined
        # the body and recorded no fact.
        yield deal_solver.Contract(name="effect", args=[])
        return
    spelled = decorators_mod.dotted_name(node)
    canonical = decorators_mod.canonical_target(spelled, aliases)
    if canonical == "deal.chain":
        if not isinstance(node, astroid.Call) or node.keywords:
            raise _unsupported_decorator(node)
        # An empty call is deal's identity decorator; the bare spelling is not.
        for argument in node.args:
            yield from _declared_contracts(
                argument,
                aliases,
                seen_by_deal=seen_by_deal and spelled == "deal.chain",
            )
        return
    name = decorators_mod.condition_contract(node, aliases)
    if name is None:
        # These decorators preserve the Python function. Guarantees are read
        # separately; fluent updates are registered when the trusted layer is
        # imported. Neither permits us to ignore an arbitrary decorator that
        # might instead hide a trusted boundary or a condition.
        if (
            canonical in {decorators_mod.GUARANTEE, decorators_mod.FLUENT_EFFECT}
            and isinstance(node, astroid.Call)
            and len(node.args) == 1
            and not node.keywords
        ):
            return
        raise _unsupported_decorator(node)
    if not isinstance(node, astroid.Call):
        raise _unsupported_decorator(node)
    # deal's own extractor has this one already -- unless it read the predicate
    # positionally and found none, in which case its empty contract was dropped
    # in ``get_contracts`` and the keyword spelling is re-read below.
    deal_read_it = not (name in _PREDICATE_CONTRACTS and not getattr(node, "args", []))
    if seen_by_deal and spelled == f"deal.{name}" and deal_read_it:
        return
    if name in _PREDICATE_CONTRACTS:
        predicate = decorators_mod.predicate_argument(node)
        if predicate is None:
            # Not reachable from a layer that imports: ``requires()`` without a
            # predicate is a TypeError. Refused rather than passed on, because
            # a contract with no predicate reaches the evaluator as
            # ``args[0]`` on an empty list.
            raise deal_solver.UnsupportedError(
                f"cannot read the predicate of @{spelled}",
            )
        _require_lambda(predicate, f"@{spelled}")
        yield deal_solver.Contract(name=name, args=[cast(AstNode, predicate)])
        return
    args = cast(list[AstNode], getattr(node, "args", []))
    yield deal_solver.Contract(name=name, args=args)


def _require_lambda(predicate: Any, spelled: str) -> None:
    """Refuse a predicate the symbolic evaluator cannot read.

    It evaluates the predicate's *body*, so it needs the lambda itself; handed
    anything else --- a named function, a partial, a callable object --- it
    returns ``None`` and the contract simply does not exist. For a
    precondition that is an obligation dropped in silence: ``@clauz3.requires``
    over a named ``small`` left ``charge(11)`` proving clean while deal went on
    raising ``PreContractError`` at run time, so the proof and the program
    disagreed about the same layer.

    Refused rather than dropped, which makes it a ``SKIP`` with a message. The
    same reasoning as the missing-predicate case above: an obligation this
    prover cannot read is one it must not pretend to have discharged.

    A sole parameter named ``_`` selects Deal's mapping calling convention.
    Reading that lambda body in the decorated function's parameter scope can
    capture a different value from the lambda's own lexical scope (issue #188).
    Refuse that convention until the evaluator models it faithfully.
    """

    if isinstance(predicate, (astroid.Lambda, ast.Lambda)):
        if _predicate_parameter_names(predicate) == {"_"}:
            raise deal_solver.UnsupportedError(
                f"{spelled} uses an unsupported '_' short-form predicate. "
                "The prover does not model its mapping argument and lexical "
                "scope. Use explicit function parameter names for preconditions "
                "and explicit result/argument names for postconditions.",
            )
        return
    raise deal_solver.UnsupportedError(
        f"{spelled} takes a predicate the prover cannot read: it evaluates the "
        "lambda's body, so the lambda has to be written in the decorator. "
        "Inline it (the named callable stays fine for deal at run time).",
    )


def _predicate_parameter_names(predicate: ast.Lambda | astroid.Lambda) -> set[str]:
    """Match the runtime's name-set test, including every parameter kind."""
    if isinstance(predicate, ast.Lambda):
        args = predicate.args
        names = {arg.arg for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
        names.update(arg.arg for arg in (args.vararg, args.kwarg) if arg is not None)
        return names
    arguments = predicate.args
    names = {
        arg.name
        for arg in [
            *(arguments.posonlyargs or []),
            *(arguments.args or []),
            *(arguments.kwonlyargs or []),
        ]
    }
    names.update(
        name for name in (arguments.vararg, arguments.kwarg) if name is not None
    )
    return names


def _concrete_contracts(
    *, source: str, source_name: str, target: str
) -> tuple[spec.ContractSpec, ...]:
    """Build oracle specifications without the symbolic literal conversion.

    The entry module is never imported or evaluated. Grammar validation allows
    only registered trusted helpers and fixed arguments; those helpers already
    ran during proving and their owning modules are selected here.
    """
    aliases = decorators_mod.import_aliases(ast.parse(source, filename=source_name))
    module = astroid.parse(source)
    result = []
    for expression in guarantee_expressions(
        source=source, source_name=source_name, target=target
    ):
        assert isinstance(expression, ast.Call)
        name = decorators_mod.canonical_target(
            decorators_mod.dotted_name(expression.func), aliases
        )
        module_name, _, helper_name = name.rpartition(".")
        helper_module = sys.modules.get(module_name)
        helper = (
            getattr(helper_module, helper_name, None)
            if helper_module is not None
            else None
        )
        if helper is None:
            raise ProverConfigError(f"cannot resolve concrete contract helper {name}")
        args = [_concrete_contract_argument(node, module) for node in expression.args]
        kwargs = {
            keyword.arg: _concrete_contract_argument(keyword.value, module)
            for keyword in expression.keywords
            if keyword.arg is not None
        }
        try:
            contract = helper(*args, **kwargs)
        except Exception as exc:
            raise ProverConfigError(
                f"cannot construct concrete contract {name}: {exc}"
            ) from exc
        if not isinstance(contract, spec.ContractSpec):
            raise ProverConfigError(
                f"concrete contract {name} must return ContractSpec"
            )
        result.append(contract)
    return tuple(result)


def _concrete_contract_argument(node: ast.expr, module: astroid.Module) -> object:
    if isinstance(node, ast.Name):
        # Resolve through this entry's own bindings, never a process-global
        # short-name scan: two trusted modules may define different UserRows.
        # The shared schema resolver also gives inline declarations the exact
        # synthetic identity retained by the proof's query-return proxies.
        try:
            definitions = list(module.igetattr(node.id))
            schemas = [
                row_schema_from_class_def(definition)
                if isinstance(definition, astroid.ClassDef)
                else None
                for definition in definitions
            ]
        except (astroid.InferenceError, RecursionError) as exc:
            raise ProverConfigError(
                f"cannot resolve concrete Row class {node.id}"
            ) from exc
        if not schemas or any(schema is None for schema in schemas):
            raise ProverConfigError(f"cannot resolve concrete Row class {node.id}")
        if any(schema is not schemas[0] for schema in schemas):
            raise ProverConfigError(f"ambiguous concrete Row class {node.id}")
        return schemas[0]
    if isinstance(node, ast.List):
        return [_concrete_contract_argument(item, module) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_concrete_contract_argument(item, module) for item in node.elts)
    if isinstance(node, ast.Set):
        return {_concrete_contract_argument(item, module) for item in node.elts}
    if isinstance(node, ast.Dict):
        return {
            _concrete_contract_argument(key, module): _concrete_contract_argument(
                value, module
            )
            for key, value in zip(node.keys, node.values, strict=True)
            if key is not None
        }
    return ast.literal_eval(node)


def prove_text(
    source: str,
    *,
    target: str | None = "main",
    import_paths: Iterable[Path] = (),
    trusted_roots: Iterable[Path] = (),
    source_name: str = "<string>",
    extra_allowed_imports: Iterable[str] = (),
    capture_observations: bool = False,
) -> list[ProofResult]:
    _validate_target_binding(
        module=ast.parse(source, filename=source_name),
        path=Path(source_name),
        target=target,
    )
    import_path_list = [Path(path) for path in import_paths]
    trusted_root_list = [Path(path) for path in trusted_roots]
    policy = build_policy(
        trusted_roots=trusted_root_list,
        import_roots=import_path_list,
        extra_allowed=extra_allowed_imports,
    )
    try:
        validate_imports(source=source, source_name=source_name, policy=policy)
    except DisallowedImportError as exc:
        raise ProverConfigError(str(exc)) from exc
    with _trusted_layer_context(trusted_root_list, import_path_list) as effects:
        try:
            validate_guarantees(source=source, source_name=source_name)
        except GuaranteeError as exc:
            raise ProverConfigError(str(exc)) from exc
        policies = load_domain_policies(
            trusted_roots=trusted_root_list,
            import_roots=import_path_list,
        )
        _validate_policy_triggers(
            policies,
            set(effects),
            load_effect_markers(trusted_root_list),
        )
        submitted_source = source
        # Refusal diagnostics use source locations even without a monitor.
        # An injected/unparsed copy must never masquerade as submitted text.
        locations: proof_monitor.SourceLocations | None = {}
        source = _inject_required_guarantees(
            source,
            target=target,
            policies=policies,
            trusted_roots=trusted_root_list,
            import_roots=import_path_list,
            locations=locations,
        )
        if source == submitted_source:
            locations = None
        results = []
        module = astroid.parse(source)
        with proof_monitor.source_scope(module, locations):
            for node in module.body:
                # The solver's generic enumeration also yields class staticmethods.
                # Only direct synchronous module functions are executable entries;
                # AsyncFunctionDef is an astroid FunctionDef subclass.
                if type(node) is not astroid.FunctionDef:
                    continue
                if target is not None and node.name != target:
                    continue
                theorem = AgentTheorem(node=node)
                proof = theorem.prove()
                observation = (
                    theorem.observation(source=source, source_name=source_name)
                    if capture_observations
                    and proof.conclusion == deal_solver.Conclusion.OK
                    else None
                )
                results.append(
                    ProofResult(name=theorem.name, proof=proof, observation=observation)
                )
        return results


def _inject_required_guarantees(
    source: str,
    *,
    target: str | None,
    policies: Sequence[DomainPolicy],
    trusted_roots: Sequence[Path],
    import_roots: Sequence[Path],
    locations: proof_monitor.SourceLocations | None = None,
) -> str:
    """Conjoin required contracts into the proof for functions that use them.

    A ``required`` contract is a TLE obligation: whenever the triggering effect
    is reachable in a target function, the contract must hold regardless of
    what the agent stated. We enforce it by rewriting the *proved* copy of the
    source — adding ``@clauz3.guarantee(<contract>())`` decorators (and the
    imports they need) so the obligation travels the same resolution path as an
    agent-written guarantee. The executed program and the approval request keep
    using the original, unmodified source.

    Triggering resolves calls transitively through local and imported helpers, so
    helper indirection cannot silently drop the obligation while the effect
    still executes, and a ``when_used`` naming something that is not a trusted
    effect is refused rather than quietly matching nothing.
    """

    if not any(policy.required for policy in policies):
        return source

    tree = ast.parse(source)
    original_positions = (
        {
            id(node): {
                "source_kind": "submitted",
                "line": node.lineno,
                "column": node.col_offset + 1,
            }
            for node in ast.walk(tree)
            if isinstance(node, ast.Call | ast.Assert)
        }
        if locations is not None
        else {}
    )
    module_aliases: dict[str, str] = {}
    changed = False

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if target is not None and node.name != target:
            continue
        used = transitive_calls(
            source, node.name, import_roots=import_roots, trusted_roots=trusted_roots
        )
        for policy in policies:
            if not domain_triggered(used, policy.when_used):
                continue
            for name in policy.required:
                module = _resolve_required_module(policy, name)
                alias = module_aliases.setdefault(
                    module,
                    f"_clauz3_required_{len(module_aliases)}",
                )
                node.decorator_list.insert(0, _guarantee_decorator(alias, name))
                changed = True

    if not changed:
        return source

    imports: list[ast.stmt] = [ast.Import(names=[ast.alias(name="clauz3")])]
    imports += [
        ast.Import(names=[ast.alias(name=module, asname=alias)])
        for module, alias in module_aliases.items()
    ]
    tree.body[0:0] = imports
    ast.fix_missing_locations(tree)
    rendered = ast.unparse(tree)
    if locations is not None:
        rebuilt = ast.parse(rendered)
        # Match structural positions in the very same injected tree, not
        # expression text: identical calls on different lines stay distinct.
        # If unparse changed its shape, report generated-source provenance
        # without guessing submitted positions. This map is diagnostic only.
        if ast.dump(tree) == ast.dump(rebuilt):
            for before, after in zip(ast.walk(tree), ast.walk(rebuilt), strict=True):
                if isinstance(after, ast.Call | ast.Assert):
                    locations[after.lineno, after.col_offset] = original_positions.get(
                        id(before), {"source_kind": "required_policy"}
                    )
    return rendered


def _validate_policy_triggers(
    policies: Sequence[DomainPolicy],
    effect_names: set[str],
    markers: Mapping[str, tuple[str, ...]],
) -> None:
    """Fail loudly when a policy's ``when_used`` is not what it says.

    A ``when_used`` names trusted effects. A token that is not one matches no
    call and would silently disable the policy; surface it as a config error
    instead so a typo --- or a ``@deal.has`` marker, which a ``when_used`` could
    once name --- cannot quietly drop enforcement.

    The quieter half is a token that is *both*: an effect called ``send`` and a
    marker ``"send"`` that ``send_bulk`` also carries. The token still
    resolves, so the check above is satisfied, while the policy has narrowed
    from two effects to one --- a program that only calls ``send_bulk`` loses
    it. Ambiguity is refused rather than resolved, because either reading is a
    guess about what the layer meant: list the effect names it covers.

    Every loaded policy, not only one carrying ``required``. What a
    ``recommended``-only policy loses is quieter still and no less serious: it
    stops matching, so its domain disappears from the coverage report rather
    than appearing there as a ``silent_gap``, and the human approving the run
    is never told the program touched it.
    """

    for policy in policies:
        unknown = [token for token in policy.when_used if token not in effect_names]
        if unknown:
            raise ProverConfigError(
                f"policy for domain {policy.domain_package!r} has "
                f"when_used token(s) {unknown!r} that name no trusted effect; "
                f"known effects: {sorted(effect_names)!r}",
            )
        for token in policy.when_used:
            also_carried = sorted(set(markers.get(token, ())) - set(policy.when_used))
            if also_carried:
                raise ProverConfigError(
                    f"policy for domain {policy.domain_package!r} has "
                    f"when_used token {token!r}, which is both a trusted effect "
                    f"and a @deal.has marker carried by {also_carried!r}. A "
                    "when_used names effects, so this policy covers "
                    f"{token!r} alone and no longer reaches the rest; name "
                    "every effect it should trigger on explicitly.",
                )


def _resolve_required_module(policy: DomainPolicy, name: str) -> str:
    """Locate the trusted module defining a required contract.

    Searched over the ``@contract`` registry rather than deal-solver's whole
    ``FUNCTIONS`` table, so a required obligation resolves to the same kind of
    helper an agent-written guarantee is allowed to name.
    """

    prefix = f"{policy.domain_package}." if policy.domain_package else ""
    matches = sorted(
        key
        for key in REGISTERED_CONTRACTS
        if key.startswith(prefix) and key.rsplit(".", 1)[-1] == name
    )
    if not matches:
        raise ProverConfigError(
            f"required contract {name!r} for domain {policy.domain_package!r} "
            "is not a registered @contract",
        )
    return matches[0].rsplit(".", 1)[0]


def _guarantee_decorator(alias: str, name: str) -> ast.Call:
    contract_call = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id=alias, ctx=ast.Load()),
            attr=name,
            ctx=ast.Load(),
        ),
        args=[],
        keywords=[],
    )
    return ast.Call(
        func=ast.Attribute(
            value=ast.Name(id="clauz3", ctx=ast.Load()),
            attr="guarantee",
            ctx=ast.Load(),
        ),
        args=[contract_call],
        keywords=[],
    )


def prove_path(
    entry_path: Path,
    *,
    trusted_roots: Iterable[Path] = (),
    import_roots: Iterable[Path] = (),
    target: str | None = "main",
    extra_allowed_imports: Iterable[str] = (),
    capture_observations: bool = False,
) -> list[ProofResult]:
    """Prove one untrusted entry file against trusted import roots."""
    entry_path = Path(entry_path)
    source = entry_path.read_text()
    trusted_root_list = list(trusted_roots)
    # Explicit roots describe the same ordered selection as prove_text/run/
    # execute. Promoting cwd here would create another image for those callers
    # and discard the provider identities they obtained after proving a path.
    import_paths = list(import_roots)
    if not import_paths:
        try:
            import_paths = expand_import_roots([Path.cwd(), entry_path.parent])
        except LayoutError as exc:
            raise ProverConfigError(str(exc)) from exc
    extra_allowed_list = list(extra_allowed_imports)
    validate_untrusted_source(source=source, path=entry_path, target=target)
    with _selected_layer_context(trusted_root_list, import_paths):
        validate_module_level(
            source,
            source_name=str(entry_path),
            trusted_roots=trusted_root_list,
            import_paths=import_paths,
            extra_allowed_imports=extra_allowed_list,
        )
        return prove_text(
            source,
            target=target,
            trusted_roots=trusted_root_list,
            import_paths=import_paths,
            source_name=str(entry_path),
            extra_allowed_imports=extra_allowed_list,
            capture_observations=capture_observations,
        )


@contextmanager
def _selected_layer_context(
    trusted_roots: Iterable[Path], import_paths: Iterable[Path]
) -> Iterator[LayerImage]:
    """Select one verified layer without executing its initializers."""
    try:
        with selected_layer(
            trusted_roots=trusted_roots, import_paths=import_paths
        ) as image:
            yield image
    except LayerStateError as exc:
        raise ProverConfigError(str(exc)) from exc


@contextmanager
def _trusted_layer_context(
    trusted_roots: list[Path], import_paths: list[Path]
) -> Iterator[dict[str, tuple[str, ...]]]:
    """Load trusted declarations with the same checks for proof and module gate.

    Importing a contract before this window would build its relations without
    known fields. A later import from the cache cannot repeat those checks.
    """
    try:
        effects = load_trusted_effects(trusted_roots)
    except DuplicateEffectError as exc:
        raise ProverConfigError(str(exc)) from exc
    # Relation origins use resolved filenames; keep symlinked layers covered.
    layer_files = {
        str(path.resolve())
        for root in trusted_roots
        for path in root.resolve().rglob("*.py")
    }
    with (
        _selected_layer_context(trusted_roots, import_paths) as image,
        spec.known_effects(effects, files=layer_files),
    ):
        try:
            loaded = _load_trusted_modules(trusted_roots, import_paths=import_paths)
            image.refresh_registrations()
            spec.validate_relations(effects, files=loaded)
        except (spec.RelationError, interp.TrustedBoundaryError, ImportError) as exc:
            raise ProverConfigError(str(exc)) from exc
        yield effects


def _load_trusted_modules(
    trusted_roots: Iterable[Path],
    *,
    import_paths: Iterable[Path],
) -> set[str]:
    """Import the trusted layer, returning the source files it covers.

    The files matter because Python imports a module once per process: a
    second proof never re-runs a contract module's ``effect(...)`` calls, so
    the relations it built the first time have to be re-checked --- and by
    file, since two layers can share a module name.
    """

    import_path_list = [path.resolve() for path in import_paths]
    trusted_root_list = [path.resolve() for path in trusted_roots]
    loaded: set[str] = set()
    candidates: list[tuple[str, Path]] = []
    for root in trusted_root_list:
        for path in sorted(root.rglob("*.py")):
            # Every file under the root is covered, ``__init__.py`` included:
            # it is imported as part of its package and may build relations of
            # its own, so leaving it out would exempt them from re-validation.
            loaded.add(str(path.resolve()))
            module_name = _module_name_for(path=path, import_paths=import_path_list)
            # ``pkg/__init__.py`` is module ``pkg``; it is imported as a side
            # effect of its siblings, but its own globals still need checking,
            # since an effect declared there is as callable as any other.
            if module_name == "__init__":
                # Bare ``__init__`` is importable, but the effect registry does
                # not model it as a package. Do not silently skip declarations.
                initializer = ast.parse(path.read_text(), filename=str(path))
                if any(
                    not isinstance(statement, ast.Pass)
                    and not (
                        isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str)
                    )
                    for statement in initializer.body
                ):
                    raise ProverConfigError(
                        f"trusted initializer {path} resolves to bare __init__; "
                        "only docstrings and pass are supported in this layout. "
                        "Use the package's parent as --import-root so its "
                        "declarations can be checked"
                    )
                continue
            module_name = module_name.removesuffix(".__init__")
            candidates.append((module_name, path))

    # Validate the whole layer before the first initializer runs. A later
    # shadowed file must not be discovered after earlier trusted imports have
    # already executed an agent-selected parent package.
    for module_name, path in candidates:
        if module_name in sys.modules:
            _require_loaded_from(sys.modules[module_name], path)
        validate_trusted_module(
            module_name,
            source=path,
            trusted_roots=trusted_root_list,
            import_roots=import_path_list,
        )

    importlib.invalidate_caches()
    for module_name, path in candidates:
        try:
            module = importlib.import_module(module_name)
        except RowSchemaError as exc:
            # Invalid declarations belong to the trusted configuration, not
            # to the submitted program. Do not hide unrelated import defects.
            raise ProverConfigError(
                f"cannot import trusted module {module_name!r} from {path}: {exc}"
            ) from exc
        _require_loaded_from(module, path)
        check_condition_signatures(module)
    return loaded


def _require_loaded_from(module: object, path: Path) -> None:
    """Require the selected cached module to match its planned source file.

    The layer coordinator selects colliding names before this check. The
    checked loader still validates each Python object before using its
    declarations, so a stale or replaced binding cannot supply authority.
    """
    namespace = (
        ModuleType.__getattribute__(module, "__dict__")
        if type(module) is ModuleType
        else {}
    )
    module_file = namespace.get("__file__")
    module_name = namespace.get("__name__")
    if type(module_file) is str and Path(module_file).resolve() == path.resolve():
        return
    safe_name = module_name if type(module_name) is str else "<nonstandard module>"
    safe_file = module_file if type(module_file) is str else "<no file>"
    raise ProverConfigError(
        f"trusted module {safe_name!r} is already loaded from "
        f"{safe_file}, not from {path}; two trusted layers "
        "share a dotted module name in this process, so the proof and the "
        "runtime would disagree about which layer is in play (issue #111). "
        "Give the layers distinct package names or prove them in separate "
        "processes"
    )


def _module_name_for(*, path: Path, import_paths: Iterable[Path]) -> str:
    path = path.resolve()
    for import_path in import_paths:
        try:
            relative = path.relative_to(import_path)
        except ValueError:
            continue
        module_path = relative.with_suffix("")
        if all(part.isidentifier() for part in module_path.parts):
            return ".".join(module_path.parts)
    raise ProverConfigError(
        f"trusted module {path} is not below an import root; pass --import-root",
    )


def check_condition_signatures(module: ModuleType) -> None:
    """Reject a condition predicate whose parameters differ from what deal passes.

    ``@clauz3.requires(pred)`` delegates to ``deal.pre``, which runs ``pred`` as
    ``pred(*args, **kwargs)`` with the caller's raw arguments; ``ensures`` runs
    its predicate as ``pred(*args, result=result, **kwargs)`` through
    ``deal.ensure``, or as ``pred(result)`` through ``deal.post`` when it takes
    one parameter. The prover evaluates each predicate body in the function's
    own parameter scope. Both assume the predicate declares exactly the
    function's parameters (plus ``result`` for a postcondition). When it does
    not, a call the function accepts -- typically one relying on a default --
    proves clean and then raises ``TypeError`` inside the predicate at runtime
    (#90). A layer still spelled ``@deal.pre`` / ``@deal.ensure`` fails the same
    way and is checked the same way.

    The comparison is exact: names, kinds and defaults. Deal would also accept
    a looser predicate such as ``lambda *args, **kwargs``, but the prover binds
    the body by parameter *name* in the function's scope, so a predicate that
    does not name the parameters is not one the proof can read anyway.

    This checks non-class callables bound directly in module globals,
    including imported or wrapped functions. It does not traverse classes,
    containers, or factories; trusted effects are module-level functions.

    Deal's ``_`` short form and active schema adapters receive a mapping
    instead of the original arguments. Their mapping/binding checks are
    separate; passing this load-time check does not make these predicates
    part of the proof expression subset.
    """
    for func in vars(module).values():
        if not callable(func) or isinstance(func, type):
            continue
        conditions = [
            contract
            for contract in deal.introspection.get_contracts(func)
            if isinstance(contract, _CONDITION_KINDS)
        ]
        if not conditions:
            continue
        where = f"{getattr(func, '__module__', module.__name__)}.{_qualname(func)}"
        try:
            signature = _unannotated(inspect.signature(func))
        except (TypeError, ValueError) as exc:
            raise ProverConfigError(
                f"{where}: carries a condition but its own signature cannot be "
                f"read ({exc}); a trusted function with a precondition or "
                "postcondition must be a Python callable with a readable "
                "signature",
            ) from exc
        for contract in conditions:
            kind = (
                "precondition"
                if isinstance(contract, deal.introspection.Pre)
                else "postcondition"
            )
            wrapped = getattr(contract, "_wrapped", None)
            predicate = getattr(wrapped, "raw_validator", None)
            if predicate is None:
                raise ProverConfigError(
                    f"{where}: cannot read the {kind} predicate back from deal "
                    f"{deal.__version__}; this check relies on "
                    "deal.introspection.ValidatedContract._wrapped.raw_validator, "
                    "which this version does not expose",
                )
            try:
                predicate_signature = _unannotated(inspect.signature(predicate))
            except (TypeError, ValueError) as exc:
                raise ProverConfigError(
                    f"{where}: {kind} predicate {predicate!r} has no readable "
                    f"signature ({exc}); deal calls it with the function's "
                    "arguments, so it must be a Python callable declaring the "
                    "same parameters",
                ) from exc
            if set(predicate_signature.parameters) == {"_"}:
                try:
                    predicate_signature.bind(object())
                except TypeError as exc:
                    raise ProverConfigError(
                        f"{where}: {kind} short-form predicate {predicate_signature} "
                        "must accept one positional mapping argument"
                    ) from exc
                _check_mapping_condition(signature, contract=contract, where=where)
                continue
            try:
                scheme = _is_scheme_validator(predicate, wrapped=wrapped)
            except Exception as exc:
                # Optional schema adapter code is a configuration boundary.
                raise ProverConfigError(
                    f"{where}: cannot classify the {kind} predicate with deal "
                    f"{deal.__version__}'s schema adapter ({exc})"
                ) from exc
            if scheme:
                _check_mapping_condition(signature, contract=contract, where=where)
                continue
            if isinstance(contract, deal.introspection.Post):
                if not _takes_one_positional(predicate_signature):
                    raise ProverConfigError(
                        f"{where}: postcondition predicate {predicate_signature} "
                        "must take exactly one positional parameter, the "
                        "result; deal calls it as predicate(result)",
                    )
                continue
            expected = signature
            actual = predicate_signature
            if isinstance(contract, deal.introspection.Ensure):
                if "result" in signature.parameters:
                    raise ProverConfigError(
                        f"{where}: has a parameter named 'result' and an ensures "
                        "postcondition; deal passes the return value as "
                        "result= as well, so the predicate can never be called",
                    )
                expected = _with_result(signature)
                actual = _result_as_keyword(predicate_signature)
            if actual != expected:
                raise ProverConfigError(
                    f"{where}: {kind} predicate {predicate_signature} does not "
                    f"match the function signature {signature}; deal calls the "
                    "predicate with the caller's arguments"
                    + (" plus result=" if expected is not signature else "")
                    + ", so it must declare the same parameters, kinds and "
                    "defaults",
                )


_CONDITION_KINDS = (
    deal.introspection.Pre,
    deal.introspection.Ensure,
    deal.introspection.Post,
)


def _qualname(func: object) -> str:
    return str(getattr(func, "__qualname__", None) or repr(func))


def _takes_one_positional(signature: inspect.Signature) -> bool:
    parameters = list(signature.parameters.values())
    return len(parameters) == 1 and parameters[0].kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


def _check_mapping_condition(
    signature: inspect.Signature, *, contract: object, where: str
) -> None:
    """Check Deal's argument-to-mapping step without invoking the predicate."""
    if isinstance(contract, deal.introspection.Post):
        # Post passes only the result, but mapping validators still bind it
        # against the decorated function's signature.
        try:
            signature.bind(object())
        except TypeError as exc:
            raise ProverConfigError(
                f"{where}: mapping postcondition cannot bind one result to "
                f"function signature {signature}"
            ) from exc
    elif "result" in signature.parameters:
        # Deal removes result= before binding in both pre/ensure mapping paths.
        raise ProverConfigError(
            f"{where}: mapping condition cannot use a function parameter named "
            "'result'; deal reserves that keyword for the return value"
        )


def _with_result(signature: inspect.Signature) -> inspect.Signature:
    """The function's signature plus the keyword ``result`` deal appends."""
    result = inspect.Parameter("result", inspect.Parameter.KEYWORD_ONLY)
    parameters = list(signature.parameters.values())
    # ``**kwargs`` must stay last; ``result`` slots in before it.
    var_kw = [p for p in parameters if p.kind is inspect.Parameter.VAR_KEYWORD]
    rest = [p for p in parameters if p.kind is not inspect.Parameter.VAR_KEYWORD]
    return signature.replace(parameters=[*rest, result, *var_kw])


def _result_as_keyword(signature: inspect.Signature) -> inspect.Signature:
    """Canonicalise a trailing positional ``result`` to keyword-only.

    deal passes ``result`` by keyword, so ``lambda cap, result`` and
    ``lambda cap, *, result`` bind identically at runtime, provided ``result``
    comes after every positional parameter and there is no ``*args``. A
    ``result`` declared earlier
    would be bound positionally to an argument and then again by keyword, so
    it is left where it is and fails the comparison.
    """
    parameters = list(signature.parameters.values())
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters):
        return signature
    positional = [
        i
        for i, p in enumerate(parameters)
        if p.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    for i, parameter in enumerate(parameters):
        if (
            parameter.name == "result"
            and parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
            and positional
            and i == positional[-1]
        ):
            parameters[i] = parameter.replace(kind=inspect.Parameter.KEYWORD_ONLY)
            # A keyword-only parameter must follow every positional one and
            # any ``*args``; re-sort by kind to compare canonically.
            parameters.sort(key=lambda p: p.kind)
            return signature.replace(parameters=parameters)
    return signature


def _is_scheme_validator(predicate: object, *, wrapped: object) -> bool:
    """Use Deal's actual adapter selection, without exempting plain predicates.

    The optional adapter is cached by Deal. Re-importing it here or checking
    ``is_valid`` alone can disagree with the runtime dispatch. The private
    method is guarded, like the private ``raw_validator`` read above.
    """
    if inspect.isroutine(predicate) or isinstance(predicate, partial):
        return False
    wrap = getattr(wrapped, "_wrap_vaa", None)
    if not callable(wrap):
        raise TypeError("the validator does not expose _wrap_vaa")
    return wrap() is not None


def _unannotated(signature: inspect.Signature) -> inspect.Signature:
    return signature.replace(
        parameters=[
            parameter.replace(annotation=inspect.Parameter.empty)
            for parameter in signature.parameters.values()
        ],
        return_annotation=inspect.Signature.empty,
    )


# Trusted-only decorators, by fully-qualified target. Aliases are resolved back
# to these before matching so ``import clauz3 as c; @c.trusted`` (or the
# legacy ``import deal as d; @d.has``) cannot slip through.
_FORBIDDEN_QUALIFIED = frozenset(
    {
        "clauz3.trusted",
        "clauz3.effects.trusted",
        "clauz3.requires",
        "clauz3.conditions.requires",
        "clauz3.ensures",
        "clauz3.conditions.ensures",
        "deal.has",
        "clauz3.spec.contract",
        "clauz3.spec.effect",
        decorators_mod.FLUENT_EFFECT,
        "clauz3.solver",
    }
)
# Trusted-only leaf names, matched against the *resolved* final attribute of any
# decorator however it is imported — including ``clauz3``'s own re-exports
# (``clauz3.contract`` / ``clauz3.effect`` are the trusted ``clauz3.spec`` /
# ``clauz3.fluent`` decorators). ``guarantee`` is intentionally absent, so
# ``@clauz3.guarantee`` stays allowed. Matching the leaf (rather than gating on
# an unqualified name) is robust to future re-exports and avoids the regression
# where ``from clauz3 import contract`` resolved to a dotted name and slipped
# past an unqualified-only check.
_FORBIDDEN_LEAF = frozenset(
    {"has", "trusted", "requires", "ensures", "contract", "effect", "solver"}
)


def validate_untrusted_source(
    *, source: str, path: Path, target: str | None = "main"
) -> None:
    """Require an unambiguous entry binding and gate agent-authored decorators.

    After entry-binding validation, two decorator rules apply:

    1. **Trusted-only decorators are refused.** ``@clauz3.trusted`` and
       friends declare effects, which is a trusted-layer privilege. An agent
       that could mark its own function as an effect boundary would be handing
       the prover a body it must not read and facts it never performed.
    2. **Everything that is not a guarantee is refused.** The only decorator the
       proved subset models is ``@clauz3.guarantee``; the interpreter has
       refused anything else since it replaced ``exec``. Silently *ignoring* an
       unrecognised decorator is what made issue #129 exploitable — a guarantee
       spelled ``@c.guarantee`` was dropped by the prover and vanished from the
       approval request, so a violating program reported ``proved!`` and the
       operator was shown "None declared". A decorator this pipeline cannot
       resolve to a known target is a refusal, never a no-op.

    Rule 1 is kept separate from rule 2 even though rule 2 subsumes it, because
    the two are different messages to different audiences: "you are not allowed
    to write this" versus "this does not mean what you think".
    """
    module = ast.parse(source, filename=str(path))
    _validate_target_binding(module=module, path=path, target=target)
    aliases = decorators_mod.import_aliases(module)
    for node in ast.walk(module):
        for decorator in getattr(node, "decorator_list", ()):
            spelled = decorators_mod.dotted_name(decorator)
            canonical = decorators_mod.canonical_target(spelled, aliases)
            leaf = canonical.rsplit(".", 1)[-1]
            if canonical in _FORBIDDEN_QUALIFIED or leaf in _FORBIDDEN_LEAF:
                raise ProverConfigError(
                    f"{path}: @{spelled} is only allowed in trusted roots",
                )
            if canonical != decorators_mod.GUARANTEE:
                raise ProverConfigError(
                    f"{path}: @{spelled or ast.unparse(decorator)} resolves to "
                    f"{canonical or 'no importable target'}, which is not a "
                    "clauz3 guarantee; agent-authored code may only be "
                    "decorated with @clauz3.guarantee(...)",
                )


def _validate_target_binding(
    *, module: ast.Module, path: Path, target: str | None
) -> None:
    # The theorem, guarantee text, and policy rewrite must describe the same
    # module-level function. Reject possible rebinding before importing or
    # executing the entry module so the interpreter cannot select another one.
    error = target_binding_error(module, target=target)
    if error is not None:
        raise ProverConfigError(f"{path}: {error}")


def validate_module_level(
    source: str,
    *,
    source_name: str,
    trusted_roots: Iterable[Path],
    import_paths: Iterable[Path],
    extra_allowed_imports: Iterable[str] = (),
) -> None:
    """Refuse untrusted source whose module scope does more than declare.

    No theorem covers module scope, so a trusted effect there -- a bare call,
    an assignment from an effect, a module-level function's default -- proved
    ``OK`` (issue #100) while ``clauz3 run`` refused it in its pre-flight. This
    runs the interpreter's own module-level pass, the gate ``run`` enforces,
    rather than restating its rules, so the two commands cannot drift apart.

    It sits beside :func:`validate_untrusted_source` at the untrusted-source
    boundary rather than inside :func:`prove_text`, which remains a raw
    function-body proof primitive. Imports must expose interpreter-supported
    names as well as satisfy the import policy. This pass does not enter
    function bodies; nested defaults remain subject to runtime refusal.
    """
    import_path_list = [Path(path) for path in import_paths]
    trusted_root_list = [Path(path) for path in trusted_roots]
    policy = build_policy(
        trusted_roots=trusted_root_list,
        import_roots=import_path_list,
        extra_allowed=extra_allowed_imports,
    )
    try:
        with _selected_layer_context(trusted_root_list, import_path_list):
            # Check the whole source, including imports inside function bodies,
            # before any trusted initialization or module-level binding occurs.
            validate_imports(source=source, source_name=source_name, policy=policy)
            interp.validate_import_origins(
                source,
                source_name=source_name,
                policy=policy,
                import_roots=import_path_list,
            )
            with _trusted_layer_context(trusted_root_list, import_path_list):
                interp.check_module_level(
                    source,
                    source_name=source_name,
                    policy=policy,
                    import_roots=import_path_list,
                )
    except (DisallowedImportError, interp.InterpError, ImportError) as exc:
        raise ProverConfigError(str(exc)) from exc
