"""Command line interface for clauz3."""

import argparse
import ast
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from clauz3 import __version__
from clauz3._vendor import deal_solver
from clauz3.approval import (
    DEFAULT_APPROVAL_TIMEOUT,
    ApprovalServiceError,
    load_mock_config,
    serve_mock_approval_service,
)
from clauz3.approval_policy import (
    ApprovalPolicyError,
    evaluate_policy,
    load_approval_policy,
)
from clauz3.approval_service import serve_approval_service
from clauz3.config import ConfigError, configure_repo
from clauz3.install import InstallError, install_layer
from clauz3.prover import ProofResult, ProverConfigError, prove_path
from clauz3.runner import (
    ApprovalDeniedError,
    RunConfigError,
    RunProofError,
    discover_trusted_roots,
    run_source,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = cast(Callable[[argparse.Namespace], int], args.handler)
    return handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clauz3",
        description="Static contract proofs for agent-authored Python.",
    )
    parser.add_argument("--version", action="version", version=f"clauz3 {__version__}")

    subparsers = parser.add_subparsers(required=True)
    prove = subparsers.add_parser(
        "prove",
        help="prove contracts for a target function",
        description="Prove contracts for a target function.",
    )
    prove.add_argument("entry", type=Path)
    _add_trusted_root_args(prove)
    _add_import_root_args(prove)
    prove.add_argument("--target", default="main")
    prove.set_defaults(handler=_prove)

    run = subparsers.add_parser(
        "run",
        help="prove, approve, and run an agent-authored program",
        description=(
            "Prove, approve, and run an agent-authored program. The approval "
            "service URL is read from CLAUZ3_APPROVAL_SERVICE, "
            "CLAUZ3_APPROVAL_URL, or .clauz3/approval-service.json."
        ),
    )
    run.add_argument(
        "program",
        nargs="?",
        help="program path, or stdin when omitted or '-'",
    )
    _add_trusted_root_args(run)
    _add_import_root_args(run)
    run.add_argument("--target", default="main")
    run.add_argument(
        "--approval-timeout",
        default=DEFAULT_APPROVAL_TIMEOUT,
        type=float,
        help=(
            f"seconds to wait for user approval (default: {DEFAULT_APPROVAL_TIMEOUT:g})"
        ),
    )
    run.set_defaults(handler=_run)

    tools = subparsers.add_parser(
        "tools",
        help="list trusted tools and contracts",
        description="List trusted tools and contracts visible from this repository.",
    )
    _add_trusted_root_args(tools)
    _add_import_root_args(tools)
    tools.set_defaults(handler=_tools)

    mock_service = subparsers.add_parser(
        "mock-approval-service",
        help="run a mock approval service for tests",
        description="Run a mock approval service for tests and demos.",
    )
    mock_service.add_argument(
        "--config",
        required=True,
        type=Path,
        help='JSON decision config, e.g. {"decision": "approved_once"}',
    )
    mock_service.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind host (default: 127.0.0.1)",
    )
    mock_service.add_argument(
        "--port",
        default=8765,
        type=int,
        help="bind port (default: 8765)",
    )
    mock_service.set_defaults(handler=_mock_approval_service)

    approval_service = subparsers.add_parser(
        "approval-service",
        help="start a localhost approval service",
        description="Start a localhost FastAPI approval service.",
    )
    approval_service.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind host (default: 127.0.0.1)",
    )
    approval_service.add_argument(
        "--port",
        default=8765,
        type=int,
        help="bind port (default: 8765)",
    )
    approval_service.add_argument(
        "--policy",
        type=Path,
        default=None,
        help="JSON approval policy authored by the policy admin for auto-decisions",
    )
    approval_service.set_defaults(handler=_approval_service)

    policy_check = subparsers.add_parser(
        "policy-check",
        help="dry-run an approval policy against a program",
        description=(
            "Report the auto-decision an approval policy would make for a "
            "program (auto_approved, auto_rejected, or ask) without starting a "
            "service or executing anything."
        ),
    )
    policy_check.add_argument(
        "program",
        nargs="?",
        help="program path, or stdin when omitted or '-'",
    )
    policy_check.add_argument(
        "--policy",
        required=True,
        type=Path,
        help="JSON approval policy to evaluate",
    )
    _add_trusted_root_args(policy_check)
    _add_import_root_args(policy_check)
    policy_check.add_argument("--target", default="main")
    policy_check.add_argument(
        "--expect",
        choices=["auto_approved", "auto_rejected", "ask"],
        default=None,
        help="exit non-zero unless the decision matches this value",
    )
    policy_check.set_defaults(handler=_policy_check)

    install = subparsers.add_parser(
        "install",
        help="install a trusted tool layer from a local path",
        description="Copy a trusted tools/ layer from a local path into a project.",
    )
    install.add_argument(
        "source",
        help=(
            "a bundled stdlib tool (such as stdlib:filesystem or stdlib:grep), "
            "a local project path containing a tools/ folder, or a tools/ folder"
        ),
    )
    install.add_argument(
        "--into",
        type=Path,
        default=None,
        help="destination project root (defaults to the current directory)",
    )
    install.add_argument(
        "--skills",
        action="store_true",
        help="also generate agents/skills/<domain>/SKILL.md for each domain",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing trusted layer at the destination",
    )
    install.set_defaults(handler=_install)

    config = subparsers.add_parser(
        "config",
        help="configure a repository for clauz3-mediated agent access",
        description=(
            "Write the default Claude Code permissions for this repository: "
            "read-only inspection plus the clauz3 CLI. Merges into an existing "
            ".claude/settings.json so it is safe to re-run."
        ),
    )
    config.add_argument(
        "--into",
        type=Path,
        default=None,
        help="project root to configure (defaults to the current directory)",
    )
    config.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing .claude/settings.json with the defaults",
    )
    config.set_defaults(handler=_config)
    return parser


def _add_trusted_root_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--trusted-root",
        dest="trusted_roots",
        action="append",
        default=[],
        type=Path,
        help="trusted package root containing effects and contracts",
    )
    parser.add_argument(
        "--trusted-roots",
        dest="trusted_roots",
        action="extend",
        nargs="+",
        type=Path,
        help="one or more trusted package roots containing effects and contracts",
    )


def _add_import_root_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--import-root",
        dest="import_roots",
        action="append",
        default=[],
        type=Path,
        help="root added to sys.path for normal Python imports",
    )
    parser.add_argument(
        "--import-roots",
        dest="import_roots",
        action="extend",
        nargs="+",
        type=Path,
        help="one or more roots added to sys.path for normal Python imports",
    )


def _prove(args: argparse.Namespace) -> int:
    try:
        results = prove_path(
            args.entry,
            trusted_roots=args.trusted_roots,
            import_roots=args.import_roots,
            target=args.target,
        )
    except ProverConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not results:
        print(f"no theorem found for target {args.target!r}")
        return 1

    failed = False
    for result in results:
        proof = result.proof
        print(f"{result.name}: {proof.conclusion.value} {proof}")
        if proof.conclusion != deal_solver.Conclusion.OK:
            failed = True
    return int(failed)


def _run(args: argparse.Namespace) -> int:
    try:
        source, source_name, import_roots = _read_run_program(
            program=args.program,
            import_roots=args.import_roots,
        )
        trusted_roots = list(args.trusted_roots)
        if not trusted_roots:
            trusted_roots = discover_trusted_roots(import_roots=import_roots)
        outcome = run_source(
            source,
            source_name=source_name,
            trusted_roots=trusted_roots,
            import_roots=import_roots,
            target=args.target,
            approval_timeout=args.approval_timeout,
        )
    except RunProofError as exc:
        _print_proofs(exc.proofs)
        return 1
    except ApprovalDeniedError as exc:
        print(f"approval: {exc.response.decision}", file=sys.stderr)
        if exc.response.feedback:
            print(f"feedback: {exc.response.feedback}", file=sys.stderr)
        return 3
    except (ApprovalServiceError, ProverConfigError, RunConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"runtime error: {exc}", file=sys.stderr)
        return 1

    _print_proofs(outcome.proofs)
    print(
        f"approval: {outcome.approval.decision} receipt={outcome.approval.receipt}",
    )
    print(f"executed: {args.target}")
    return 0


def _tools(args: argparse.Namespace) -> int:
    import_roots = _default_import_roots(args.import_roots)
    trusted_roots = list(args.trusted_roots)
    if not trusted_roots:
        trusted_roots = discover_trusted_roots(import_roots=import_roots)
    if not trusted_roots:
        print("no trusted tools found")
        return 0

    for root in trusted_roots:
        for item in _iter_trusted_items(root):
            print(item)
    return 0


def _install(args: argparse.Namespace) -> int:
    into = args.into or Path.cwd()
    try:
        result = install_layer(
            args.source,
            into=into,
            generate_skills=args.skills,
            force=args.force,
        )
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for domain in result.domains:
        print(f"installed trusted layer: {result.dest_tools / domain}")
    for skill in result.skills:
        print(f"generated skill: {skill}")
    return 0


def _config(args: argparse.Namespace) -> int:
    into = args.into or Path.cwd()
    try:
        result = configure_repo(into=into, force=args.force)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if result.created:
        print(f"wrote claude config: {result.path}")
    elif result.added:
        added = ", ".join(result.added)
        print(f"updated claude config: {result.path} (added: {added})")
    else:
        print(f"claude config already up to date: {result.path}")
    return 0


def _mock_approval_service(args: argparse.Namespace) -> int:
    try:
        config = load_mock_config(args.config)
        serve_mock_approval_service(config, host=args.host, port=args.port)
    except KeyboardInterrupt:
        return 130
    except ApprovalServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _approval_service(args: argparse.Namespace) -> int:
    try:
        policy = load_approval_policy(args.policy) if args.policy else None
        serve_approval_service(host=args.host, port=args.port, policy=policy)
    except KeyboardInterrupt:
        return 130
    except (ApprovalServiceError, ApprovalPolicyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _policy_check(args: argparse.Namespace) -> int:
    try:
        source, _source_name, import_roots = _read_run_program(
            program=args.program,
            import_roots=args.import_roots,
        )
        trusted_roots = list(args.trusted_roots)
        if not trusted_roots:
            trusted_roots = discover_trusted_roots(import_roots=import_roots)
        policy = load_approval_policy(args.policy)
    except ApprovalPolicyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    request = {
        "program": source,
        "target": args.target,
        "trusted_roots": [str(path) for path in trusted_roots],
        "import_roots": [str(path) for path in import_roots],
    }
    decision = evaluate_policy(policy, request)
    label = decision.decision if decision is not None else "ask"
    if decision is not None:
        print(f"{label}: {decision.rule} ({decision.reason or 'no reason'})")
    else:
        print(f"{label}: no rule matched; a human would decide")

    if args.expect is not None and label != args.expect:
        print(f"error: expected {args.expect}, got {label}", file=sys.stderr)
        return 1
    return 0


def _read_run_program(
    *,
    program: str | None,
    import_roots: list[Path],
) -> tuple[str, str, list[Path]]:
    roots = _default_import_roots(import_roots)
    if program is None or program == "-":
        return sys.stdin.read(), "<stdin>", roots

    path = Path(program)
    return path.read_text(), str(path), [*roots, path.parent]


def _default_import_roots(import_roots: list[Path]) -> list[Path]:
    if import_roots:
        return list(import_roots)
    return [Path.cwd()]


def _print_proofs(results: list[ProofResult]) -> None:
    for result in results:
        proof = result.proof
        print(f"{result.name}: {proof.conclusion.value} {proof}")


def _iter_trusted_items(root: Path) -> list[str]:
    items: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        module = ast.parse(path.read_text(), filename=str(path))
        for node in module.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            decorators = [
                _decorator_name(decorator) for decorator in node.decorator_list
            ]
            signature = _signature(node)
            rel_path = path.relative_to(root.parent.parent)
            if any(name in {"deal.has", "has"} for name in decorators):
                items.append(f"effect {rel_path}:{node.name}{signature}")
            if any(name in {"contract", "clauz3.spec.contract"} for name in decorators):
                items.append(f"contract {rel_path}:{node.name}{signature}")
    return items


def _signature(node: ast.FunctionDef) -> str:
    args = ", ".join(arg.arg for arg in node.args.args)
    return f"({args})"


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
