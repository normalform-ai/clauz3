"""Command line interface for clauz3."""

import argparse
import ast
import json
import os
import secrets
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from clauz3 import __version__, interp
from clauz3 import decorators as decorators_mod
from clauz3._vendor import deal_solver
from clauz3.approvals.authority import ApprovalAuthorizationError, ApprovedLayer
from clauz3.approvals.client import (
    APPROVAL_DECISION_TOKEN_ENV,
    APPROVAL_TOKEN_ENV,
    DEFAULT_APPROVAL_TIMEOUT,
    ApprovalServiceError,
    load_mock_config,
    serve_mock_approval_service,
)
from clauz3.approvals.policy import (
    ApprovalPolicyError,
    evaluate_policy,
    load_approval_policy,
)
from clauz3.approvals.receipts import (
    SIGNING_KEY_ENV,
    VERIFY_KEY_ENV,
    ReceiptError,
    generate_keypair,
)
from clauz3.approvals.service import serve_approval_service
from clauz3.config import ConfigError, configure_repo
from clauz3.diagnostics.proof import ProofDeadlineExceeded
from clauz3.libraries.discovery import (
    format_libraries,
    global_library_root,
    library_records,
)
from clauz3.libraries.install import InstallError, install_library
from clauz3.libraries.layout import LayoutError, expand_import_roots
from clauz3.libtest import LibTestError, run_lib_tests
from clauz3.lint import LintError, format_violations, lint_paths
from clauz3.prover import ProofResult, ProverConfigError, prove_path
from clauz3.runner import (
    ApprovalDeniedError,
    RunConfigError,
    RunProgramError,
    RunProofError,
    discover_trusted_roots,
    run_source,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = cast(Callable[[argparse.Namespace], int], args.handler)
    try:
        return handler(args)
    except LayoutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clauz3",
        description="Static contract proofs for agent-authored Python.",
    )
    parser.add_argument("--version", action="version", version=f"clauz3 {__version__}")

    subparsers = parser.add_subparsers(required=True)
    start = subparsers.add_parser(
        "start", help="start an approval service and a restricted agent session"
    )
    start.add_argument("agent", choices=["claude"])
    start.add_argument(
        "--no-browser",
        action="store_true",
        help="print the private approval URL without opening it",
    )
    start.add_argument(
        "--port",
        type=int,
        default=0,
        help="localhost approval port (default: choose a free port)",
    )
    start.add_argument("--policy", type=Path, help="operator-authored approval policy")
    start.add_argument("--model", help="agent model name")
    start.add_argument("--prompt", help="initial agent prompt")
    start.add_argument(
        "--setup",
        action="store_true",
        help="choose libraries even when the project already has tools",
    )
    start.add_argument(
        "--unrestricted",
        action="store_true",
        help="debug: enable native agent tools with normal permissions",
    )
    start.add_argument(
        "--skills-dir",
        type=Path,
        action="append",
        default=[],
        help="select inert SKILL.md documents from a folder (repeatable)",
    )
    start.set_defaults(handler=_start)
    ui = subparsers.add_parser(
        "ui", help="open a unified agent conversation and approval workspace"
    )
    ui.add_argument("agent", choices=["claude"], nargs="?", default="claude")
    ui.add_argument(
        "--no-browser",
        action="store_true",
        help="print the private login URL without opening a browser",
    )
    ui.add_argument(
        "--port",
        type=int,
        default=0,
        help="local service port (default: choose an available port)",
    )
    ui.add_argument("--policy", type=Path, help="approval policy file")
    ui.add_argument("--model", help="agent model for new conversations")
    ui.add_argument("--prompt", help="start a new conversation with this prompt")
    ui.add_argument(
        "--setup", action="store_true", help="open library setup in the browser"
    )
    ui.add_argument(
        "--skills-dir",
        type=Path,
        action="append",
        default=[],
        help="select inert SKILL.md documents from a folder (repeatable)",
    )
    ui.set_defaults(handler=_start, unrestricted=False, browser_ui=True)
    resume = subparsers.add_parser(
        "resume", help="resume a managed session with a fresh approval service"
    )
    resume.add_argument("session_id")
    resume.add_argument("--no-browser", action="store_true")
    resume.add_argument("--port", type=int, default=0)
    resume.add_argument("--prompt")
    resume.set_defaults(handler=_resume)

    prove = subparsers.add_parser(
        "prove",
        help="prove contracts for a target function",
        description=(
            "Prove contracts for a target function. Each entry file is parsed "
            "and proved on its own; files are never combined into one module."
        ),
    )
    prove.add_argument("entry", type=Path, nargs="+", metavar="ENTRY")
    _add_trusted_root_args(prove)
    _add_import_root_args(prove)
    _add_allow_import_args(prove)
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
    _add_allow_import_args(run)
    run.add_argument("--target", default="main")
    run.add_argument(
        "--approval-timeout",
        default=DEFAULT_APPROVAL_TIMEOUT,
        type=float,
        help=(
            f"seconds to wait for user approval (default: {DEFAULT_APPROVAL_TIMEOUT:g})"
        ),
    )
    run.add_argument(
        "--step-budget",
        default=interp.DEFAULT_STEP_BUDGET,
        type=int,
        help=(
            "maximum nodes the interpreter may evaluate "
            f"(default: {interp.DEFAULT_STEP_BUDGET}). Exhausting it aborts a "
            "proved, approved program mid-run, after some of its effects have "
            "already fired."
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

    libraries = subparsers.add_parser(
        "libraries", help="list/search bundled, external and user-wide libraries"
    )
    libraries.add_argument("query", nargs="*", help="search names and descriptions")
    libraries.add_argument("--json", action="store_true", help="output JSON metadata")
    libraries.set_defaults(handler=_libraries)

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
        description=(
            "Start a localhost FastAPI approval service. With --policy, "
            "configure authorized trusted/import roots explicitly."
        ),
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
    _add_trusted_root_args(approval_service)
    _add_import_root_args(approval_service)
    approval_service.add_argument(
        "--no-auth",
        action="store_true",
        help="disable bearer-token auth on state-changing endpoints (insecure)",
    )
    approval_service.set_defaults(handler=_approval_service)

    approval_keygen = subparsers.add_parser(
        "approval-keygen",
        help="generate an Ed25519 approval-signing keypair",
        description=(
            "Generate an Ed25519 keypair for approval receipts. Set the signing "
            "key in the approval service's environment and the verifying key in "
            "the runner's environment; the runner refuses to run without it."
        ),
    )
    approval_keygen.set_defaults(handler=_approval_keygen)

    policy_check = subparsers.add_parser(
        "policy-check",
        help="dry-run an approval policy against a program",
        description=(
            "Report the auto-decision an approval policy would make for a "
            "program (auto_approved, auto_rejected, or ask) without starting a "
            "service or executing the program. Trusted modules are imported."
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
        help="install a trusted library into a project or the user-wide store",
        description=(
            "Copy a trusted tools/ layer and declared library dependencies into "
            "the project's .clauz3/tools/ (or the user-wide store with --global)."
        ),
    )
    install.add_argument(
        "source",
        help=(
            "a bundled stdlib tool (such as stdlib:filesystem or stdlib:grep), "
            "global:<domain>, a git source, a local project containing tools/, "
            "or a tools/ folder"
        ),
    )
    destination = install.add_mutually_exclusive_group()
    destination.add_argument(
        "--into",
        type=Path,
        default=None,
        help="destination project root (defaults to the current directory)",
    )
    destination.add_argument(
        "-g",
        "--global",
        dest="globally",
        action="store_true",
        help="install into the user-wide store for later project selection",
    )
    install.add_argument(
        "--skills",
        action="store_true",
        help="also generate .clauz3/skills/<domain>/SKILL.md for each domain",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing trusted layer at the destination",
    )
    install.set_defaults(handler=_install)

    test = subparsers.add_parser(
        "test",
        help="run a trusted library's bundled test suite",
        description=(
            "Run a library's tests/Justfile with just. The source is resolved "
            "the same way as install: a bundled stdlib tool (such as "
            "stdlib:filesystem), a local project path containing a tests/ "
            "folder, or a tests/ folder itself."
        ),
    )
    test.add_argument(
        "source",
        help=(
            "a bundled stdlib tool (such as stdlib:filesystem or stdlib:grep), "
            "a local project path containing a tests/ folder, or a tests/ folder"
        ),
    )
    test.add_argument(
        "--recipe",
        default="test",
        help="just recipe to run (default: test)",
    )
    test.set_defaults(handler=_test)

    lint = subparsers.add_parser(
        "lint",
        help="check trusted bodies against conditions and effect markers",
        description=(
            "Check trusted modules with deal's linter, including native "
            "@clauz3.requires and @clauz3.ensures conditions. Optional "
            "@deal.has declarations also enable checks for missing effect "
            "markers, such as syscall for a subprocess call. "
            "Exits non-zero on any violation."
        ),
    )
    lint.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Python files or directories to check",
    )
    lint.set_defaults(handler=_lint)

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
        help=(
            "ordered import root; explicit roots replace the default roots; "
            "does not authorize nontrusted library source"
        ),
    )
    parser.add_argument(
        "--import-roots",
        dest="import_roots",
        action="extend",
        nargs="+",
        type=Path,
        help=(
            "ordered import roots; explicit roots replace the default roots; "
            "do not authorize nontrusted library source"
        ),
    )


def _add_allow_import_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allow-import",
        dest="extra_allowed_imports",
        action="append",
        default=[],
        metavar="MODULE",
        help=(
            "extra module (top-level or dotted prefix) the untrusted source "
            "may import; origin and supported-surface checks still apply; "
            "may be repeated"
        ),
    )


def _prove(args: argparse.Namespace) -> int:
    # Entries share one process (and its import cost) but are each proved on
    # their own. The exit status is the worst seen: 2 for a configuration
    # error, 1 for an unproved obligation, 0 when every entry proves.
    entries: list[Path] = args.entry
    label = len(entries) > 1
    return max(_prove_entry(entry, args, label=label) for entry in entries)


def _prove_entry(entry: Path, args: argparse.Namespace, *, label: bool) -> int:
    prefix = f"{entry}: " if label else ""
    try:
        results = prove_path(
            entry,
            trusted_roots=args.trusted_roots,
            import_roots=args.import_roots,
            target=args.target,
            extra_allowed_imports=args.extra_allowed_imports,
        )
    except (ProverConfigError, OSError, SyntaxError, UnicodeDecodeError) as exc:
        # An unreadable or unparseable entry is reported like a configuration
        # error, so it cannot cut a multi-entry run short.
        message = str(exc)
        # Most configuration errors already name the entry; don't repeat it.
        if label and not message.startswith(str(entry)):
            message = prefix + message
        print(f"error: {message}", file=sys.stderr)
        return 2
    if not results:
        print(f"{prefix}no theorem found for target {args.target!r}")
        return 1

    failed = False
    for result in results:
        proof = result.proof
        print(f"{prefix}{result.name}: {proof.conclusion.value} {proof}")
        if proof.conclusion != deal_solver.Conclusion.OK:
            failed = True
    return int(failed)


def _start(args: argparse.Namespace) -> int:
    from clauz3.sessions.launcher import StartError, start_session

    try:
        return start_session(
            agent=args.agent,
            port=args.port,
            policy=args.policy,
            open_browser=not args.no_browser,
            model=args.model,
            prompt=args.prompt,
            unrestricted=args.unrestricted,
            setup=args.setup,
            skills_dirs=args.skills_dir,
            browser_ui=getattr(args, "browser_ui", False),
        )
    except (StartError, OSError, ValueError, ApprovalPolicyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _resume(args: argparse.Namespace) -> int:
    from clauz3.sessions.launcher import StartError, resume_session

    try:
        return resume_session(
            args.session_id,
            open_browser=not args.no_browser,
            port=args.port,
            prompt=args.prompt,
        )
    except (StartError, OSError, ValueError, ApprovalPolicyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


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
            extra_allowed_imports=args.extra_allowed_imports,
            step_budget=args.step_budget,
        )
    except ProofDeadlineExceeded as exc:
        print(
            f"proof timeout: {exc}; approval was not requested "
            "and the program did not execute.",
            file=sys.stderr,
        )
        return 2
    except RunProofError as exc:
        _print_proofs(exc.proofs)
        return 1
    except RunProgramError as exc:
        # A proved, approved program that raised at run time. Exit 1, the same
        # code the ``exec`` path produced for an uncaught Python exception --
        # not the exit 2 that means "this source should not have been run".
        print(f"runtime error: {exc}", file=sys.stderr)
        return 1
    except ApprovalDeniedError as exc:
        print(f"approval: {exc.response.decision}", file=sys.stderr)
        if exc.response.feedback:
            print(f"feedback: {exc.response.feedback}", file=sys.stderr)
        return 3
    except (
        ApprovalServiceError,
        LayoutError,
        ProverConfigError,
        RunConfigError,
        ReceiptError,
    ) as exc:
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
    into = global_library_root() if args.globally else (args.into or Path.cwd())
    try:
        results = install_library(
            args.source,
            into=into,
            generate_skills=args.skills,
            force=args.force,
            globally=args.globally,
        )
    except (InstallError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for result in results:
        for domain in result.domains:
            print(f"installed trusted layer: {result.dest_tools / domain}")
            if args.globally:
                print(f"select in a project: clauz3 install global:{domain}")
        for skill in result.skills:
            print(f"generated skill: {skill}")
    return 0


def _libraries(args: argparse.Namespace) -> int:
    try:
        records = library_records(Path.cwd(), " ".join(args.query))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(records, indent=2) if args.json else format_libraries(records))
    return 0


def _test(args: argparse.Namespace) -> int:
    try:
        result = run_lib_tests(args.source, recipe=args.recipe)
    except LibTestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return result.returncode


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


def _resolve_approval_token() -> str:
    """Bearer token for the approval service: from env, else freshly generated.

    The same ``CLAUZ3_APPROVAL_TOKEN`` is read client-side by
    ``submit_approval_request`` so an operator can point a runner at an
    auth-enabled service by exporting it in both environments.
    """

    return os.environ.get(APPROVAL_TOKEN_ENV) or secrets.token_urlsafe(32)


def _resolve_decision_token(auth_token: str) -> str:
    """Approver credential, distinct from the runner's submission token.

    Read from ``CLAUZ3_APPROVAL_DECISION_TOKEN`` or freshly generated. Only the
    person (or tool) that decides requests should hold it; ``clauz3 run`` never
    needs it.
    """

    token = os.environ.get(APPROVAL_DECISION_TOKEN_ENV) or secrets.token_urlsafe(32)
    if secrets.compare_digest(token.encode(), auth_token.encode()):
        raise ApprovalServiceError(
            f"{APPROVAL_DECISION_TOKEN_ENV} must differ from {APPROVAL_TOKEN_ENV}"
        )
    return token


def _approval_service(args: argparse.Namespace) -> int:
    try:
        policy = load_approval_policy(args.policy) if args.policy else None
        authorized_layers: tuple[ApprovedLayer, ...] = ()
        if args.trusted_roots or args.import_roots:
            authorized_layers = (
                ApprovedLayer(
                    trusted_roots=tuple(args.trusted_roots),
                    import_roots=tuple(_default_import_roots(args.import_roots)),
                ),
            )
        elif policy is not None:
            raise ApprovalAuthorizationError(
                "--policy requires operator-configured roots: supply "
                "--trusted-root and/or --import-root; the request cannot "
                "authorize its own layer"
            )
        auth_token = None if args.no_auth else _resolve_approval_token()
        decision_token = (
            None if auth_token is None else _resolve_decision_token(auth_token)
        )
        serve_approval_service(
            host=args.host,
            port=args.port,
            policy=policy,
            auth_token=auth_token,
            decision_token=decision_token,
            authorized_layers=authorized_layers,
        )
    except KeyboardInterrupt:
        return 130
    except (
        ApprovalServiceError,
        ApprovalPolicyError,
        ApprovalAuthorizationError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _approval_keygen(_args: argparse.Namespace) -> int:
    signing_key, verify_key = generate_keypair()
    # Two copy-pasteable assignments: the private signing key on the service,
    # the public verifying key on the runner. Keep the signing key secret.
    print("# approval service (signs receipts) — keep secret:")
    print(f"{SIGNING_KEY_ENV}={signing_key}")
    print("# runner (verifies receipts):")
    print(f"{VERIFY_KEY_ENV}={verify_key}")
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
        authorized_layer = ApprovedLayer(
            trusted_roots=tuple(trusted_roots),
            import_roots=tuple(import_roots),
        )
        request = {
            "program": source,
            "target": args.target,
            "trusted_roots": [str(path) for path in trusted_roots],
            "import_roots": [str(path) for path in import_roots],
        }
        decision = evaluate_policy(
            policy, request, authorized_layers=(authorized_layer,)
        )
    except (ApprovalPolicyError, ApprovalAuthorizationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

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
    # Match prove_path: adding a root changes the selected layer's identity.
    # Explicit roots retain their order; only the default includes the entry.
    if not import_roots:
        roots = expand_import_roots([*roots, path.parent])
    return path.read_text(), str(path), roots


def _default_import_roots(import_roots: list[Path]) -> list[Path]:
    """Explicit roots literally, or the working directory with its ``.clauz3``."""
    if import_roots:
        return list(import_roots)
    return expand_import_roots([Path.cwd()])


def _print_proofs(results: list[ProofResult]) -> None:
    for result in results:
        proof = result.proof
        print(f"{result.name}: {proof.conclusion.value} {proof}")


def _lint(args: argparse.Namespace) -> int:
    try:
        violations = lint_paths(args.paths)
    except LintError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if violations:
        print(format_violations(violations))
    return 1 if violations else 0


#: How ``@contract`` may be spelled. Contracts are matched on source text; an
#: effect is decided by :func:`clauz3.decorators.is_trusted_effect`, the same
#: function the prover uses, so what this lists and what the prover treats as a
#: boundary cannot disagree --- a text match omitted an aliased ``@c.trusted``
#: and wrongly listed a bare ``@deal.has()``, which is not a boundary.
_CONTRACT_DECORATORS = frozenset({"contract", "clauz3.spec.contract"})


def _iter_trusted_items(root: Path) -> list[str]:
    items: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        module = ast.parse(path.read_text(), filename=str(path))
        aliases = decorators_mod.import_aliases(module)
        for node in module.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            decorators = [
                _decorator_name(decorator) for decorator in node.decorator_list
            ]
            signature = _signature(node)
            rel_path = path.relative_to(root.parent.parent)
            if decorators_mod.is_trusted_effect(node, aliases):
                items.append(f"effect {rel_path}:{node.name}{signature}")
            if any(name in _CONTRACT_DECORATORS for name in decorators):
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
