"""Own a local approval service and a restricted agent's lifetime.

The terminal and browser belong to the operator. Only submission credentials
reach the agent; signing and decision credentials stay in the service process.
This is harness-level confinement, not an OS sandbox against the same user.
"""

import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import webbrowser
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from clauz3.approvals.client import (
    APPROVAL_DECISION_TOKEN_ENV,
    APPROVAL_SERVICE_ENV,
    APPROVAL_TOKEN_ENV,
)
from clauz3.approvals.policy import load_approval_policy
from clauz3.approvals.receipts import SIGNING_KEY_ENV, VERIFY_KEY_ENV, generate_keypair
from clauz3.libraries.install import InstallError
from clauz3.libraries.layout import domain_name, expand_import_roots
from clauz3.libraries.setup import choose_libraries
from clauz3.runner import discover_trusted_roots
from clauz3.sessions import providers
from clauz3.sessions.records import (
    SessionRecord,
    load_session,
    save_session,
    selection_fingerprints,
    session_lease,
)
from clauz3.sessions.runs import PROOF_TIMEOUT_ENV, proof_timeout
from clauz3.sessions.skills import snapshot_skills

STARTUP_TIMEOUT = 15.0
# The native-command configuration below was verified with this CLI version.
MIN_CLAUDE_VERSION = (2, 1, 283)
SESSION_INSTRUCTIONS = """You are running inside a clauz3 session.
First read_skill(id="builtin:clauz3-authoring") for the supported Python subset
and submission workflow. Use list_skills for previews of tested recipes and
selected documentation; read_skill(id) returns inert text, never permissions.
Use mcp__clauz3__tools to discover the installed public effects and contracts.
Use mcp__clauz3__libraries (clauz3 libraries) to list/search available libraries.
Use mcp__clauz3__status to inspect a backgrounded run. Only an explicit
awaiting_approval phase means a pending approval request exists. A timeout or
unresolved proof never executes; do not infer success from a long-running call.
This is read-only catalogue access; ask the user to install missing libraries
and restart the session. Only the selected project libraries can execute.
Submit Python source directly to mcp__clauz3__run (the clauz3 run command).
Import installed effects and contract helpers from tools.<domain>.trusted.
Define main() with @clauz3.guarantee(...) decorators calling those helpers with
literal arguments. State meaningful guarantees matching the user's request.
The tool proves the program, waits for the user to approve in their browser,
and executes only after verifying a signed receipt. Failed proofs or denied
approvals do not execute. Do not invent tools or install new trusted code.
If these tools are unavailable or return no result, report that execution has
not happened.
"""
CLI_SESSION_INSTRUCTIONS = """
The user can inspect the session bridge with /mcp or exit and
restart clauz3 start. Native /status shows Claude session information, not
clauz3 proof or approval status. Native /skills does not list clauz3's selected
documentation; use list_skills and read_skill for that. Native commands are
operator controls, not additional tools or permission to bypass clauz3.
"""


class StartError(Exception):
    """The session could not start or its approval service failed."""


class _Terminated(BaseException):
    pass


def _check_claude_version(executable: str) -> None:
    minimum = ".".join(map(str, MIN_CLAUDE_VERSION))
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=STARTUP_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StartError(
            f"could not check Claude Code version; {minimum} or newer is required"
        ) from exc
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+) \(Claude Code\)", result.stdout.strip())
    if result.returncode or match is None:
        raise StartError(
            f"could not identify Claude Code version; {minimum} or newer is required"
        )
    if tuple(map(int, match.groups())) < MIN_CLAUDE_VERSION:
        raise StartError(
            f"Claude Code {minimum} or newer is required for restricted native "
            f"commands; found {match.group(0)}. Update Claude Code and retry."
        )


@contextmanager
def _handle_termination() -> Iterator[None]:
    def terminate(signum: int, frame: FrameType | None) -> None:
        raise _Terminated

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _wait_ready(process: subprocess.Popen[bytes], url: str) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    opener = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise StartError("approval service exited before becoming ready")
        try:
            with opener.open(f"{url}/health", timeout=0.25) as response:
                if json.load(response) == {"ok": True}:
                    return
        except (OSError, URLError, ValueError):
            pass
        time.sleep(0.05)
    raise StartError("approval service did not become ready within 15 seconds")


def _bridge_command(cwd: Path, roots: list[Path]) -> list[str]:
    return [
        sys.executable,
        "-P",
        "-m",
        "clauz3.sessions.tools",
        str(cwd),
        *[str(root) for root in roots],
    ]


def _session_bridge_command(
    cwd: Path, roots: list[Path], skills_snapshot: Path | None
) -> list[str]:
    command = _bridge_command(cwd, roots)
    if skills_snapshot is not None:
        command += ["--skills-snapshot", str(skills_snapshot)]
    return command


def _check_bridge(
    cwd: Path,
    roots: list[Path],
    env: dict[str, str],
    skills_snapshot: Path | None = None,
) -> None:
    # Probe in the exact interpreter and working directory Claude will use.
    # A development lockfile can hide incompatible dependencies in uvx/wheels.
    try:
        result = subprocess.run(
            [*_session_bridge_command(cwd, roots, skills_snapshot), "--check"],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=STARTUP_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise StartError(
            "MCP bridge startup check timed out; agent not started"
        ) from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-4000:]
        raise StartError(
            "MCP bridge could not start; agent not started. "
            "Check the clauz3 installation (requires mcp>=1.0,<2).\n" + detail
        )
    try:
        names = json.loads(result.stdout)
    except ValueError as exc:
        raise StartError("MCP bridge startup check returned invalid output") from exc
    expected = ["libraries", "run", "status", "tools", "list_skills", "read_skill"]
    if (
        not isinstance(names, list)
        or not all(isinstance(name, str) for name in names)
        or sorted(names) != sorted(expected)
    ):
        raise StartError("MCP bridge is missing the expected session tools")


def _claude_command(
    executable: str,
    *,
    cwd: Path,
    roots: list[Path],
    env: dict[str, str],
    model: str | None,
    prompt: str | None,
    unrestricted: bool,
    skills_snapshot: Path | None = None,
    session_id: str | None = None,
    resume: bool = False,
) -> list[str]:
    # Inline configuration is session-local: no project or user files to edit
    # or restore. The bridge accepts source, never a shell command or root flags.
    bridge = _session_bridge_command(cwd, roots, skills_snapshot)
    config = {
        "mcpServers": {
            "clauz3": {
                "command": bridge[0],
                "args": bridge[1:],
                "env": env,
            }
        }
    }
    command = [
        executable,
        "--strict-mcp-config",
        "--mcp-config",
        json.dumps(config),
        "--setting-sources",
        "",
        "--settings",
        json.dumps(
            {
                "disableAllHooks": True,
                "disableClaudeAiConnectors": True,
                "enabledPlugins": {},
                # Keep operator commands without loading executable skills.
                # Empty setting sources exclude user/project skill directories.
                "disableBundledSkills": True,
                "disableSkillShellExecution": True,
                "syncClaudeAiSkills": False,
                "syncClaudeAiPlugins": False,
                # /doctor remains available despite disableBundledSkills.
                "skillOverrides": {"doctor": "off"},
            }
        ),
        "--no-chrome",
        "--allowedTools",
        "mcp__clauz3__run,mcp__clauz3__tools,mcp__clauz3__libraries,mcp__clauz3__status,"
        "mcp__clauz3__list_skills,mcp__clauz3__read_skill",
        "--append-system-prompt",
        SESSION_INSTRUCTIONS
        + CLI_SESSION_INSTRUCTIONS
        + (
            "Debug mode: you may also use native tools directly; they bypass "
            "clauz3 proofs and approval."
            if unrestricted
            else "All file access and effects must go through installed tools "
            "and clauz3 run."
        ),
    ]
    if not unrestricted:
        command += ["--restricted", "--tools", "", "--permission-mode", "dontAsk"]
    else:
        command += ["--permission-mode", "default"]
    if session_id is not None:
        command += ["--resume" if resume else "--session-id", session_id]
    if model is not None:
        command += ["--model", model]
    if prompt is not None:
        command += ["--", prompt]
    return command


def start_session(
    *,
    agent: str,
    port: int = 0,
    policy: Path | None = None,
    open_browser: bool = True,
    model: str | None = None,
    prompt: str | None = None,
    unrestricted: bool = False,
    setup: bool = False,
    skills_dirs: list[Path] | None = None,
    browser_ui: bool = False,
    _resume: SessionRecord | None = None,
) -> int:
    """Start in the current directory, returning the agent's exit status."""
    try:
        providers.check_mode(agent, unrestricted, browser_ui=browser_ui)
    except ValueError as exc:
        raise StartError(str(exc)) from exc
    use_acp = browser_ui or agent != "claude"
    if os.name != "posix":
        raise StartError("clauz3 start currently supports macOS and Linux")
    try:
        proof_timeout()
    except ValueError as exc:
        raise StartError(str(exc)) from exc
    executable: str | None
    if agent != "claude":
        try:
            agent_command = providers.command(agent)
            providers.check_runtime(agent, agent_command, unrestricted)
        except ValueError as exc:
            raise StartError(str(exc)) from exc
        executable = agent_command[0]
    else:
        executable = shutil.which("claude-agent-acp" if browser_ui else agent)
        agent_command = [executable] if executable else []
    if executable is None:
        if browser_ui:
            raise StartError(
                "install the UI adapter: npm install -g "
                "@agentclientprotocol/claude-agent-acp@0.89.1"
            )
        raise StartError("claude is not on PATH; install Claude Code and sign in first")
    if browser_ui and agent == "claude":
        from clauz3.sessions.chat import check_adapter

        try:
            check_adapter(executable)
        except ValueError as exc:
            raise StartError(str(exc)) from exc
    if not 0 <= port <= 65535:
        raise StartError("--port must be between 0 and 65535")
    if policy is not None:
        policy = policy.resolve()
        load_approval_policy(policy)
    cwd = Path.cwd().resolve()
    roots = discover_trusted_roots(import_roots=expand_import_roots([cwd]))
    if _resume is None and not browser_ui and (setup or not roots):
        if not sys.stdin.isatty():
            raise StartError(
                "no installed tools or --setup requested without a terminal; "
                "browse with clauz3 libraries, then install with "
                "clauz3 install stdlib:filesystem"
            )
        try:
            if not choose_libraries(cwd):
                return 0
        except KeyboardInterrupt:
            return 130
        except (InstallError, OSError, ValueError) as exc:
            raise StartError(str(exc)) from exc
        roots = discover_trusted_roots(import_roots=expand_import_roots([cwd]))
    folders = [
        folder.expanduser().resolve(strict=True) for folder in (skills_dirs or [])
    ]
    skills = snapshot_skills(folders)
    if folders:
        print(f"Selected skill documents: {len(skills)}", file=sys.stderr)
        if not skills:
            print(
                "Warning: no SKILL.md documents found in the selected folders.",
                file=sys.stderr,
            )
    selection = selection_fingerprints(cwd, roots, policy, skills)
    if _resume is not None:
        changed = [
            name for name in selection if selection[name] != _resume.selection.get(name)
        ]
        if changed:
            raise StartError(
                "cannot resume: selected "
                + ", ".join(changed)
                + " changed; use clauz3 start to review a new session selection"
            )
    if not use_acp:
        _check_claude_version(executable)
    record = _resume or SessionRecord(
        session_id=str(uuid4()),
        cwd=str(cwd),
        agent=agent,
        model=model,
        unrestricted=unrestricted,
        policy=str(policy) if policy else None,
        skills_dirs=list(map(str, folders)),
        selection=selection,
    )
    signing, verifying = generate_keypair()
    submission = secrets.token_urlsafe(32)
    decision = secrets.token_urlsafe(32)
    # Never inherit stale clauz3 credentials, including a private signing key
    # previously exported by the operator in this shell.
    base_env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("CLAUZ3_APPROVAL")
        and k
        not in {
            "CLAUZ3_UI_CONFIG",
            "CLAUZ3_RUN_STATUS_FILE",
            "CLAUZ3_SESSION_LIFETIME_SOCKET",
        }
    }
    # Both urllib and harness transports must reach the local service directly,
    # including in shells configured to proxy all HTTP traffic.
    no_proxy = base_env.get("no_proxy", base_env.get("NO_PROXY", ""))
    base_env["no_proxy"] = base_env["NO_PROXY"] = f"{no_proxy},127.0.0.1,localhost"
    service: subprocess.Popen[bytes] | None = None
    harness: subprocess.Popen[bytes] | None = None
    try:
        with (
            _handle_termination(),
            session_lease(record.session_id),
            tempfile.TemporaryDirectory(prefix="clauz3-session-") as session_directory,
            socket.socket() as listener,
            tempfile.TemporaryFile() as log,
        ):
            skills_snapshot = None
            if folders:
                skills_snapshot = Path(session_directory) / "skills.json"
                skills_snapshot.write_text(
                    json.dumps([skill.record() for skill in skills])
                )
            _check_bridge(cwd, roots, base_env, skills_snapshot)
            print(
                "MCP bridge checked: run, tools, libraries, status, "
                "list_skills, read_skill. "
                + (
                    "Agent slash commands are disabled in this workspace. "
                    if use_acp
                    else "Use /mcp for connection diagnostics. "
                )
                + "Use list_skills/read_skill for selected documentation.",
                file=sys.stderr,
            )
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen()
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            runner_env = {
                APPROVAL_SERVICE_ENV: url,
                APPROVAL_TOKEN_ENV: submission,
                VERIFY_KEY_ENV: verifying,
            }
            if PROOF_TIMEOUT_ENV in base_env:
                runner_env[PROOF_TIMEOUT_ENV] = base_env[PROOF_TIMEOUT_ENV]
            service_env = {
                **base_env,
                APPROVAL_TOKEN_ENV: submission,
                APPROVAL_DECISION_TOKEN_ENV: decision,
                SIGNING_KEY_ENV: signing,
            }
            if use_acp:
                config_path = Path(session_directory) / "ui.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "agent": agent,
                            "unrestricted": unrestricted,
                            "select_provider": browser_ui,
                            "command": agent_command,
                            "cwd": str(cwd),
                            "bridge": _session_bridge_command(
                                cwd, roots, skills_snapshot
                            ),
                            "runner_env": runner_env,
                            "model": model,
                            "prompt": prompt,
                            "status_file": str(Path(session_directory) / "runs.json"),
                            "libraries": [domain_name(cwd, root) for root in roots],
                            "manage_libraries": True,
                            "show_libraries": setup or not roots,
                        }
                    )
                )
                config_path.chmod(0o600)
                if browser_ui:
                    service_env["CLAUZ3_UI_CONFIG"] = str(config_path)
            service_command = [
                sys.executable,
                "-P",
                "-m",
                "clauz3.sessions.service",
                str(listener.fileno()),
                str(cwd),
                json.dumps([str(p) for p in roots]),
            ]
            if policy is not None:
                service_command.append(str(policy))
            try:
                service = subprocess.Popen(
                    service_command,
                    cwd=cwd,
                    env=service_env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    pass_fds=(listener.fileno(),),
                    start_new_session=True,
                )
                try:
                    _wait_ready(service, url)
                except StartError:
                    # Surface the known lease error without exposing arbitrary
                    # service logs, which may contain operator credentials.
                    log.seek(0, os.SEEK_END)
                    log.seek(max(0, log.tell() - 8192))
                    if b"This project already has a running workspace UI" in log.read():
                        raise StartError(
                            "This project already has a running workspace UI"
                        ) from None
                    raise
                listener.close()
                login_url = f"{url}/ui/login?key={decision}"
                print(f"Approval service: {url}", file=sys.stderr)
                print(
                    f"Approver UI (keep private): {login_url}",
                    file=sys.stderr,
                    flush=True,
                )
                if open_browser:
                    try:
                        opened = webbrowser.open(login_url)
                    except (webbrowser.Error, OSError):
                        opened = False
                    if not opened:
                        print(
                            "Open the approver URL above in a browser.", file=sys.stderr
                        )
                if browser_ui:
                    print(
                        "Clauz3 workspace ready. Press Ctrl-C to stop.", file=sys.stderr
                    )
                    while service.poll() is None:
                        time.sleep(0.2)
                    raise StartError("workspace service exited")
                if unrestricted:
                    print(
                        "Debug mode: native agent tools can bypass clauz3 approval.",
                        file=sys.stderr,
                    )
                if _resume is None:
                    save_session(record)
                print(f"Clauz3 session: {record.session_id}", file=sys.stderr)
                harness = subprocess.Popen(
                    (
                        [
                            sys.executable,
                            "-P",
                            "-m",
                            "clauz3.sessions.terminal",
                            str(config_path),
                            record.session_id,
                            *(["--resume"] if _resume else []),
                        ]
                        if use_acp
                        else _claude_command(
                            executable,
                            cwd=cwd,
                            roots=roots,
                            env=runner_env,
                            model=model,
                            prompt=prompt,
                            unrestricted=unrestricted,
                            skills_snapshot=skills_snapshot,
                            session_id=record.session_id,
                            resume=_resume is not None,
                        )
                    ),
                    cwd=cwd,
                    env={
                        **base_env,
                        **runner_env,
                        "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
                    },
                )
                while True:
                    if service.poll() is not None:
                        raise StartError("approval service exited; stopping the agent")
                    try:
                        code = harness.wait(timeout=0.2)
                        return code if code >= 0 else 128 - code
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                _stop(harness)
                _stop(service)
                if service is not None:
                    print("Approval service stopped.", file=sys.stderr)
                if harness is not None:
                    print(
                        f"Resume with: clauz3 resume {record.session_id}",
                        file=sys.stderr,
                    )
    except KeyboardInterrupt:
        return 130
    except _Terminated:
        return 143


def resume_session(
    session_id: str,
    *,
    open_browser: bool = True,
    port: int = 0,
    prompt: str | None = None,
) -> int:
    """Resume only a recorded selection, with a new supervised approval service."""
    record = load_session(session_id)
    previous = Path.cwd()
    try:
        os.chdir(record.cwd)
        return start_session(
            agent=record.agent,
            model=record.model,
            unrestricted=record.unrestricted,
            policy=Path(record.policy) if record.policy else None,
            skills_dirs=[Path(folder) for folder in record.skills_dirs],
            open_browser=open_browser,
            port=port,
            prompt=prompt,
            _resume=record,
        )
    finally:
        os.chdir(previous)
