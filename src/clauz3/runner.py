"""Runtime path for proved and approved clauz3 programs."""

import ast
import builtins
import hashlib
import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from clauz3._vendor import deal_solver
from clauz3.approval import (
    ApprovalResponse,
    ApprovalServiceError,
    configured_service_url,
    submit_approval_request,
)
from clauz3.policy import compute_coverage, load_domain_policies
from clauz3.prover import (
    ProofResult,
    prove_text,
    validate_untrusted_source,
)

FORBIDDEN_IMPORT_ROOTS = {
    "builtins",
    "ctypes",
    "http",
    "importlib",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "smtplib",
    "socket",
    "subprocess",
    "sys",
    "urllib",
}
FORBIDDEN_BUILTINS = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}
FORBIDDEN_ATTRIBUTES = {
    "__bases__",
    "__builtins__",
    "__class__",
    "__dict__",
    "__globals__",
    "__getattribute__",
    "__mro__",
    "__subclasses__",
}


@dataclass(frozen=True)
class RunOutcome:
    proofs: list[ProofResult]
    approval: ApprovalResponse
    request: dict[str, object]


class RunConfigError(Exception):
    """The run configuration or source program is invalid."""


class RunProofError(Exception):
    """The source program did not prove."""

    def __init__(self, proofs: list[ProofResult]) -> None:
        super().__init__("program did not prove")
        self.proofs = proofs


class ApprovalDeniedError(Exception):
    """The approval service did not approve execution."""

    def __init__(self, response: ApprovalResponse) -> None:
        super().__init__(response.decision)
        self.response = response


def discover_trusted_roots(*, import_roots: Iterable[Path]) -> list[Path]:
    """Discover default trusted roots such as tools/email/trusted."""

    roots: list[Path] = []
    seen: set[Path] = set()
    for import_root in import_roots:
        tools_dir = import_root / "tools"
        if not tools_dir.is_dir():
            continue
        for candidate in sorted(tools_dir.glob("*/trusted")):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            roots.append(candidate)
    return roots


def run_source(
    source: str,
    *,
    source_name: str,
    trusted_roots: Iterable[Path],
    import_roots: Iterable[Path],
    target: str = "main",
    approval_service_url: str | None = None,
    approval_timeout: float | None = None,
) -> RunOutcome:
    """Prove, request approval for, and execute an inline program."""

    import_root_list = [Path(path) for path in import_roots]
    trusted_root_list = [Path(path) for path in trusted_roots]
    _validate_runtime_source(source=source, source_name=source_name)

    proofs = prove_text(
        source,
        target=target,
        import_paths=import_root_list,
        trusted_roots=trusted_root_list,
        source_name=source_name,
    )
    if not proofs:
        raise RunConfigError(f"no theorem found for target {target!r}")
    if any(proof.proof.conclusion != deal_solver.Conclusion.OK for proof in proofs):
        raise RunProofError(proofs)

    request = build_approval_request(
        source=source,
        source_name=source_name,
        target=target,
        proofs=proofs,
        trusted_roots=trusted_root_list,
        import_roots=import_root_list,
    )
    service_url = approval_service_url or configured_service_url()
    if service_url is None:
        raise ApprovalServiceError(
            "no approval service configured; set CLAUZ3_APPROVAL_SERVICE",
        )
    if approval_timeout is None:
        approval = submit_approval_request(service_url, request)
    else:
        approval = submit_approval_request(
            service_url,
            request,
            timeout=approval_timeout,
        )
    if not approval.approved:
        raise ApprovalDeniedError(approval)
    if not approval.receipt:
        raise ApprovalServiceError("approval response did not include a receipt")

    _execute_source(
        source,
        source_name=source_name,
        target=target,
        import_roots=import_root_list,
        request_id=str(request["request_id"]),
        receipt=approval.receipt,
    )
    return RunOutcome(proofs=proofs, approval=approval, request=request)


def build_approval_request(
    *,
    source: str,
    source_name: str,
    target: str,
    proofs: list[ProofResult],
    trusted_roots: Iterable[Path],
    import_roots: Iterable[Path],
) -> dict[str, object]:
    program_hash = hashlib.sha256(source.encode()).hexdigest()
    request_id = f"clr_{program_hash[:12]}"
    guarantees = _extract_guarantees(source=source, target=target)
    policies = load_domain_policies(
        trusted_roots=trusted_roots,
        import_roots=import_roots,
    )
    return {
        "schema_version": 1,
        "kind": "clauz3.run",
        "request_id": request_id,
        "program_sha256": program_hash,
        "source_name": source_name,
        "target": target,
        "trusted_roots": [str(path) for path in trusted_roots],
        "import_roots": [str(path) for path in import_roots],
        "guarantees": guarantees,
        "coverage": compute_coverage(
            source=source,
            guarantees=guarantees,
            policies=policies,
        ),
        "proofs": [
            {
                "name": result.name,
                "conclusion": result.proof.conclusion.value,
                "description": result.proof.description,
            }
            for result in proofs
        ],
        "program": source,
    }


def _extract_guarantees(*, source: str, target: str) -> list[str]:
    module = ast.parse(source)
    for node in module.body:
        if not isinstance(node, ast.FunctionDef) or node.name != target:
            continue
        guarantees: list[str] = []
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if _expr_name(decorator.func) not in {"clauz3.guarantee", "guarantee"}:
                continue
            if decorator.args:
                guarantees.append(ast.unparse(decorator.args[0]))
        return guarantees
    return []


def _validate_runtime_source(*, source: str, source_name: str) -> None:
    validate_untrusted_source(source=source, path=Path(source_name))
    module = ast.parse(source, filename=source_name)
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _reject_import(alias.name, source_name=source_name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                _reject_import(node.module, source_name=source_name)
        elif isinstance(node, ast.Call):
            name = _expr_name(node.func)
            if name in FORBIDDEN_BUILTINS:
                raise RunConfigError(f"{source_name}: {name} is not allowed in run")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRIBUTES or node.attr.startswith("__"):
                raise RunConfigError(
                    f"{source_name}: access to {node.attr} is not allowed in run",
                )
        elif isinstance(node, ast.Name) and (
            node.id == "__builtins__" or node.id.startswith("__")
        ):
            raise RunConfigError(
                f"{source_name}: access to {node.id} is not allowed in run",
            )


def _reject_import(module_name: str, *, source_name: str) -> None:
    root = module_name.partition(".")[0]
    if root in FORBIDDEN_IMPORT_ROOTS:
        raise RunConfigError(f"{source_name}: import of {root} is not allowed in run")


def _execute_source(
    source: str,
    *,
    source_name: str,
    target: str,
    import_roots: Iterable[Path],
    request_id: str,
    receipt: str,
) -> None:
    with (
        _temporary_sys_path(import_roots),
        _temporary_environ(
            {
                "CLAUZ3_REQUEST_ID": request_id,
                "CLAUZ3_APPROVAL_RECEIPT": receipt,
            }
        ),
    ):
        namespace: dict[str, Any] = {
            "__name__": "__clauz3_run__",
            "__file__": source_name,
            "__builtins__": _safe_builtins(),
        }
        exec(compile(source, source_name, "exec"), namespace)
        func = namespace.get(target)
        if not callable(func):
            raise RunConfigError(f"{source_name}: target {target!r} is not callable")
        func()


def _safe_builtins() -> dict[str, Any]:
    safe = vars(builtins).copy()
    for name in FORBIDDEN_BUILTINS:
        safe.pop(name, None)
    safe["__import__"] = _guarded_import
    return safe


def _guarded_import(
    name: str,
    globals: dict[str, object] | None = None,
    locals: dict[str, object] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> ModuleType:
    _reject_import(name, source_name="<clauz3-run>")
    module = builtins.__import__(name, globals, locals, fromlist, level)
    if not isinstance(module, ModuleType):
        raise ImportError(f"{name} did not resolve to a module")
    return module


def _expr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _expr_name(node.value)
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


class _temporary_environ:
    def __init__(self, updates: Mapping[str, str]) -> None:
        self._updates = dict(updates)
        self._originals: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self._updates.items():
            self._originals[key] = os.environ.get(key)
            os.environ[key] = value

    def __exit__(self, *_exc_info: object) -> None:
        for key, original in self._originals.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
