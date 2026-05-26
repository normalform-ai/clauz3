import ast
import importlib
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import astroid
from deal.linter._extractors import get_contracts

from clauz3._vendor import deal_solver
from clauz3._vendor.deal_solver._types import AstNode
from clauz3.policy import DomainPolicy, calls_in, load_domain_policies


@dataclass(frozen=True)
class ProofResult:
    name: str
    proof: deal_solver.Proof

    @property
    def ok(self) -> bool:
        return self.proof.conclusion == deal_solver.Conclusion.OK


class ProverConfigError(Exception):
    """The requested proof configuration is not valid."""


class AgentTheorem(deal_solver.Theorem):
    @staticmethod
    def get_contracts(func: astroid.FunctionDef) -> Iterator[deal_solver.Contract]:
        for contract in get_contracts(func):
            yield deal_solver.Contract(
                name=contract.name,
                args=cast(list[AstNode], contract.args),
            )

    @staticmethod
    def get_guarantees(func: astroid.FunctionDef) -> Iterator[astroid.NodeNG]:
        decorators = func.decorators
        if decorators is None:
            return
        for decorator in decorators.nodes:
            if not isinstance(decorator, astroid.Call):
                continue
            if decorator.func.as_string() not in {"clauz3.guarantee", "guarantee"}:
                continue
            if not decorator.args:
                continue
            yield decorator.args[0]


def prove_text(
    source: str,
    *,
    target: str | None = "main",
    import_paths: Iterable[Path] = (),
    trusted_roots: Iterable[Path] = (),
    source_name: str = "<string>",
) -> list[ProofResult]:
    import_path_list = [Path(path) for path in import_paths]
    trusted_root_list = [Path(path) for path in trusted_roots]
    with _temporary_sys_path(import_path_list):
        _load_trusted_modules(trusted_root_list, import_paths=import_path_list)
        policies = load_domain_policies(
            trusted_roots=trusted_root_list,
            import_roots=import_path_list,
        )
        source = _inject_required_guarantees(
            source,
            target=target,
            policies=policies,
        )
        results = []
        for theorem in AgentTheorem.from_text(source):
            if target is not None and theorem.name != target:
                continue
            results.append(ProofResult(name=theorem.name, proof=theorem.prove()))
        return results


def _inject_required_guarantees(
    source: str,
    *,
    target: str | None,
    policies: Sequence[DomainPolicy],
) -> str:
    """Conjoin required contracts into the proof for functions that use them.

    A ``required`` contract is a TLE obligation: whenever the triggering effect
    is reachable in a target function, the contract must hold regardless of
    what the agent stated. We enforce it by rewriting the *proved* copy of the
    source — adding ``@clauz3.guarantee(<contract>())`` decorators (and the
    imports they need) so the obligation travels the same resolution path as an
    agent-written guarantee. The executed program and the approval request keep
    using the original, unmodified source.
    """

    if not any(policy.required for policy in policies):
        return source

    from clauz3._vendor.deal_solver._funcs import FUNCTIONS

    tree = ast.parse(source)
    module_aliases: dict[str, str] = {}
    changed = False

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if target is not None and node.name != target:
            continue
        used = calls_in(node)
        for policy in policies:
            if not (set(policy.when_used) & used):
                continue
            for name in policy.required:
                module = _resolve_required_module(policy, name, FUNCTIONS)
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
    return ast.unparse(tree)


def _resolve_required_module(
    policy: DomainPolicy,
    name: str,
    functions: Iterable[str],
) -> str:
    prefix = f"{policy.domain_package}." if policy.domain_package else ""
    matches = [
        key
        for key in functions
        if key.startswith(prefix) and key.rsplit(".", 1)[-1] == name
    ]
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
) -> list[ProofResult]:
    """Prove one untrusted entry file against trusted import roots."""
    entry_path = Path(entry_path)
    source = entry_path.read_text()
    validate_untrusted_source(source=source, path=entry_path)
    return prove_text(
        source,
        target=target,
        trusted_roots=trusted_roots,
        import_paths=[*import_roots, Path.cwd(), entry_path.parent],
        source_name=str(entry_path),
    )


def prove_paths(
    paths: Iterable[Path],
    *,
    target: str | None = "main",
) -> list[ProofResult]:
    """Compatibility wrapper for callers that already pass a single entry path."""
    path_list = list(paths)
    if len(path_list) != 1:
        raise ProverConfigError(
            "prove_paths now accepts one entry file only; use prove_path(..., "
            "trusted_roots=...) for trusted code",
        )
    return prove_path(path_list[0], target=target)


def _load_trusted_modules(
    trusted_roots: Iterable[Path],
    *,
    import_paths: Iterable[Path],
) -> None:
    import_path_list = [path.resolve() for path in import_paths]
    importlib.invalidate_caches()
    for root in trusted_roots:
        root = root.resolve()
        for path in sorted(root.rglob("*.py")):
            if path.name == "__init__.py":
                continue
            module_name = _module_name_for(path=path, import_paths=import_path_list)
            importlib.import_module(module_name)


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


def validate_untrusted_source(*, source: str, path: Path) -> None:
    module = ast.parse(source, filename=str(path))
    for node in ast.walk(module):
        decorators = getattr(node, "decorator_list", ())
        for decorator in decorators:
            name = _decorator_name(decorator)
            if name in {
                "contract",
                "clauz3.spec.contract",
                "deal.has",
                "has",
                "clauz3.solver",
                "solver",
            }:
                raise ProverConfigError(
                    f"{path}: @{name} is only allowed in trusted roots",
                )


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _decorator_name(node.value)
        if base:
            return f"{base}.{node.attr}"
        return node.attr
    return ""


class _temporary_sys_path:
    def __init__(self, paths: Iterable[Path]) -> None:
        self._paths = [str(path) for path in paths]

    def __enter__(self) -> None:
        sys.path[:0] = self._paths

    def __exit__(self, *_exc_info: object) -> None:
        del sys.path[: len(self._paths)]
