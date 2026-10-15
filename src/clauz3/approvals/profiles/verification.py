"""Bounded independent verification before remembering or matching a contract.

The worker receives no approval credentials and never executes the submitted
program. Authorized trusted modules are operator-controlled executable code.
"""

import ast
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from importlib.metadata import distribution
from pathlib import Path
from typing import cast

from clauz3.approvals.authority import ApprovedLayer

VERIFICATION_TIMEOUT = 20.0


def verify(
    request: Mapping[str, object], layer: ApprovedLayer, *, project: str
) -> dict[str, object]:
    payload = dict(request)
    payload["trusted_roots"] = [str(p) for p in layer.trusted_roots]
    payload["import_roots"] = [str(p) for p in layer.import_roots]
    payload["project"] = project
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUZ3_APPROVAL_")}
    result = subprocess.run(
        [sys.executable, "-P", "-m", "clauz3.approvals.profiles.verification"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=VERIFICATION_TIMEOUT,
        env=env,
        cwd=project,
    )
    if result.returncode:
        raise ValueError(
            "independent verification failed; approve once or revise the program"
        )
    value: object = json.loads(result.stdout)
    if not isinstance(value, dict) or not isinstance(value.get("signature"), str):
        raise ValueError("invalid profile verification result")
    return cast(dict[str, object], value)


def _verified_basis(request: dict[str, object]) -> dict[str, object]:
    # Imports are intentionally confined to the child process.
    from clauz3._vendor import deal_solver
    from clauz3.guarantees import contract_descriptions, contract_documentation
    from clauz3.imports_policy import build_policy
    from clauz3.prover import _selected_layer_context, prove_text
    from clauz3.runner import (
        _preflight_structure,
        _validate_runtime_source,
        build_approval_request,
    )

    source = str(request["program"])
    target = str(request.get("target", "main"))
    roots = [Path(p) for p in cast(list[str], request["trusted_roots"])]
    paths = [Path(p) for p in cast(list[str], request["import_roots"])]
    policy = build_policy(trusted_roots=roots, import_roots=paths)
    _validate_runtime_source(
        source=source, source_name="<profile>", policy=policy, target=target
    )
    with _selected_layer_context(roots, paths) as layer:
        _preflight_structure(
            source=source,
            source_name="<profile>",
            target=target,
            policy=policy,
            import_roots=paths,
        )
        proofs = prove_text(
            source,
            target=target,
            trusted_roots=roots,
            import_paths=paths,
            source_name="<profile>",
        )
        if not proofs or any(
            p.proof.conclusion != deal_solver.Conclusion.OK for p in proofs
        ):
            raise ValueError("only fully proved programs can have profile signatures")
        verified = build_approval_request(
            source=source,
            source_name="<profile>",
            target=target,
            proofs=proofs,
            trusted_roots=roots,
            import_roots=paths,
        )
        for field in ("program_sha256", "guarantees", "coverage"):
            if request.get(field) != verified[field]:
                raise ValueError(
                    f"submitted {field} differs from independent verification"
                )
        if not verified["guarantees"]:
            raise ValueError("a remembered contract needs explicit guarantees")
        # Row-class arguments require a schema identity, not merely the class
        # name. Keep those requests manual until that identity is represented.
        for expression in cast(list[str], verified["guarantees"]):
            call = ast.parse(expression, mode="eval").body
            assert isinstance(call, ast.Call)
            for argument in [*call.args, *(kw.value for kw in call.keywords)]:
                ast.literal_eval(argument)
        # Exact syntax plus exact import bindings is deliberately conservative:
        # aliases/default arguments need not match semantically in this MVP.
        imports = [
            ast.dump(node, include_attributes=False)
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Import | ast.ImportFrom)
        ]
        basis: dict[str, object] = {
            "schema": 1,
            "project": request["project"],
            "owner": os.getuid(),
            "target": target,
            "imports": imports,
            "guarantees": sorted(cast(list[str], verified["guarantees"])),
            "coverage": verified["coverage"],
            "trusted_roots": verified["trusted_roots"],
            "import_roots": verified["import_roots"],
            "layer": layer.profile_source_identity(),
            "engine": _engine_identity(),
        }
        basis["signature"] = hashlib.sha256(
            json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        # Presentation is a historical snapshot, not a new matching rule.
        # The trusted source identity above already binds the docstring source.
        basis["contract_documentation"] = contract_documentation(
            source=source, target=target
        )
        basis["contract_descriptions"] = contract_descriptions(
            source=source, target=target
        )
        return basis


def _engine_identity() -> str:
    """Invalidate on engine/dependency upgrades, including editable installs."""
    files = set(Path(__file__).resolve().parents[2].rglob("*.py"))
    files.add(Path(sys.executable).resolve())
    # Z3 loads its solver through ctypes, so the shared library is absent from
    # sys.modules. Bind its actual implementation, not just the Python wrapper.
    solver = distribution("z3-solver")
    native = [
        entry
        for entry in solver.files or ()
        if Path(str(entry)).suffix in {".so", ".dylib", ".dll"}
    ]
    if not native:
        raise ValueError("cannot identify the installed native solver")
    files.update(Path(str(solver.locate_file(entry))).resolve() for entry in native)
    for module in tuple(sys.modules.values()):
        origin = getattr(module, "__file__", None)
        if origin and Path(origin).is_file():
            files.add(Path(origin).resolve())
    digest = hashlib.sha256(sys.version.encode())
    for path in sorted(files):
        digest.update(str(path).encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


if __name__ == "__main__":
    # Trusted module print output must not corrupt the protocol.
    from contextlib import redirect_stdout

    with redirect_stdout(sys.stderr):
        result = _verified_basis(json.load(sys.stdin))
    print(json.dumps(result))
