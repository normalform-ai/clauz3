"""Launcher lifecycle and real MCP → runner → signed approval integration."""

import json
import shutil
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import pytest

from clauz3.cli import main
from clauz3.libraries.discovery import installed_domains
from clauz3.sessions import launcher

EMAIL = Path(__file__).resolve().parents[1] / "examples/email"


@pytest.fixture(autouse=True)
def session_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "clauz3-home"))


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Install the example into a path containing spaces to exercise argv handling.
    folder = tmp_path / "my project"
    folder.mkdir()
    assert main(["install", str(EMAIL), "--into", str(folder)]) == 0
    monkeypatch.chdir(folder)
    return folder


def fake_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    *,
    version: str = "2.1.283 (Claude Code)",
) -> None:
    script = tmp_path / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        f"    print({version!r})\n"
        "    raise SystemExit(0)\n" + body
    )
    script.chmod(0o700)
    monkeypatch.setattr(
        "clauz3.sessions.launcher.shutil.which", lambda name: str(script)
    )


@pytest.mark.parametrize("resume", [False, True])
def test_command_has_only_session_tools(tmp_path: Path, resume: bool) -> None:
    command = launcher._claude_command(
        "/agent/claude",
        cwd=tmp_path,
        roots=[tmp_path / "tools/email/trusted"],
        env={"CLAUZ3_APPROVAL_TOKEN": "submission"},
        model="test-model",
        prompt="--dangerously-skip-permissions",
        unrestricted=False,
        session_id="session-id",
        resume=resume,
    )
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in command
    assert "--disable-slash-commands" not in command
    assert "--restricted" in command
    instructions = command[command.index("--append-system-prompt") + 1]
    assert "inspect the session bridge with /mcp" in instructions
    assert command[command.index("--resume" if resume else "--session-id") + 1] == (
        "session-id"
    )
    assert "--no-chrome" in command
    assert set(command[command.index("--allowedTools") + 1].split(",")) == {
        "mcp__clauz3__run",
        "mcp__clauz3__tools",
        "mcp__clauz3__libraries",
        "mcp__clauz3__status",
        "mcp__clauz3__list_skills",
        "mcp__clauz3__read_skill",
    }
    settings = json.loads(command[command.index("--settings") + 1])
    assert settings["disableAllHooks"] is True
    assert settings["disableClaudeAiConnectors"] is True
    assert settings["disableBundledSkills"] is True
    assert settings["disableSkillShellExecution"] is True
    assert settings["syncClaudeAiSkills"] is False
    assert settings["syncClaudeAiPlugins"] is False
    assert settings["enabledPlugins"] == {}
    assert settings["skillOverrides"] == {"doctor": "off"}
    assert command[-2:] == ["--", "--dangerously-skip-permissions"]
    config = json.loads(command[command.index("--mcp-config") + 1])
    assert set(config["mcpServers"]) == {"clauz3"}
    bridge = config["mcpServers"]["clauz3"]
    assert bridge["command"] == sys.executable
    assert bridge["args"] == [
        "-P",
        "-m",
        "clauz3.sessions.tools",
        str(tmp_path),
        str(tmp_path / "tools/email/trusted"),
    ]


def test_start_real_service_and_bridge(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in (
        "CLAUZ3_APPROVAL_SIGNING_KEY",
        "CLAUZ3_APPROVAL_DECISION_TOKEN",
        "CLAUZ3_APPROVAL_SERVICE",
        "CLAUZ3_APPROVAL_TOKEN",
        "CLAUZ3_APPROVAL_VERIFY_KEY",
        "CLAUZ3_APPROVAL_URL",
        "CLAUZ3_APPROVAL_RECEIPT",
        "CLAUZ3_UI_CONFIG",
        "CLAUZ3_RUN_STATUS_FILE",
        "CLAUZ3_SESSION_LIFETIME_SOCKET",
    ):
        monkeypatch.setenv(name, "stale-secret")
    monkeypatch.setenv("TEST_CASE", str(EMAIL / "cases/only_bob_pass.py"))
    monkeypatch.setenv("CLAUZ3_PROOF_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    # A package in the working directory must not shadow the installed launcher.
    (project / "clauz3.py").write_text("raise RuntimeError('shadowed')\n")
    fake_agent(
        tmp_path,
        monkeypatch,
        """
import json, os, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from clauz3.mcp import MCPClient

assert "CLAUZ3_APPROVAL_SIGNING_KEY" not in os.environ
assert "CLAUZ3_APPROVAL_DECISION_TOKEN" not in os.environ
assert "CLAUZ3_APPROVAL_URL" not in os.environ
assert "CLAUZ3_APPROVAL_RECEIPT" not in os.environ
assert "CLAUZ3_UI_CONFIG" not in os.environ
assert "CLAUZ3_RUN_STATUS_FILE" not in os.environ
assert "CLAUZ3_SESSION_LIFETIME_SOCKET" not in os.environ
assert os.environ["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"
assert not any(v == "stale-secret" for k, v in os.environ.items()
               if k.startswith("CLAUZ3_APPROVAL"))
config = json.loads(sys.argv[sys.argv.index("--mcp-config") + 1])
bridge = config["mcpServers"]["clauz3"]
assert set(bridge["env"]) == {
    "CLAUZ3_APPROVAL_SERVICE", "CLAUZ3_APPROVAL_TOKEN", "CLAUZ3_APPROVAL_VERIFY_KEY",
    "CLAUZ3_PROOF_TIMEOUT_SECONDS",
}
assert bridge["env"]["CLAUZ3_PROOF_TIMEOUT_SECONDS"] == "20"
url = os.environ["CLAUZ3_APPROVAL_SERVICE"]
assert json.load(urlopen(url + "/health")) == {"ok": True}
# A submission credential cannot inspect the approver UI.
try:
    urlopen(Request(url + "/ui", headers={
        "Authorization": "Bearer " + os.environ["CLAUZ3_APPROVAL_TOKEN"]
    }))
except HTTPError as exc:
    assert exc.code == 401
else:
    raise AssertionError("submission token granted approver access")
with MCPClient(command=bridge["command"], args=bridge["args"],
               env={**os.environ, **bridge["env"]}) as client:
    definitions = client.list_tools()
    assert {t["name"] for t in definitions} == {
        "run", "tools", "libraries", "status", "list_skills", "read_skill"
    }
    available = client.call("libraries", query="email")
    assert "gh:normalform-ai/clauz3-tools-assistant" in available
    assert "clauz3 install" in available
    assert not Path(".clauz3/tools/filesystem").exists()
    run_schema = next(t for t in definitions if t["name"] == "run")["input_schema"]
    assert set(run_schema["properties"]) == {"source"}
    catalog = client.call("tools", query="send_email")
    assert ".clauz3/tools/email/trusted/effects.py" in catalog
    assert "send_email" in catalog
    assert client.call("tools", query="/etc/passwd") == "No installed tools match."
    source = Path(os.environ["TEST_CASE"]).read_text()
    shadowed = client.call("run", source=source)
    assert "exit code: 2" in shadowed, shadowed
    assert "executed:" not in shadowed, shadowed
    Path("clauz3.py").unlink()
    approved = client.call("run", source=source)
    assert "exit code: 0" in approved, approved
    assert "approval: auto_approved receipt=clz2." in approved, approved
    denied = client.call(
        "run", source=source.replace("bob@example.com", "ceo@example.com")
    )
    assert "exit code: 3" in denied, denied
    assert "approval: auto_rejected" in denied, denied
    assert "executed:" not in denied, denied
    bad = source.replace('send_email("bob@example.com"', 'send_email("eve@example.com"')
    failed = client.call("run", source=bad)
    assert "exit code: 1" in failed, failed
    assert "approval:" not in failed, failed
    refused = client.call(
        "run", source="import os\\ndef main(): os.system('touch escaped')"
    )
    assert "executed:" not in refused, refused
    assert not Path("escaped").exists()
Path("session-result.json").write_text(json.dumps({"url": url, "approved": approved}))
sys.exit(7)
""",
    )
    assert (
        main(
            [
                "start",
                "claude",
                "--no-browser",
                "--policy",
                str(EMAIL / "approval-policy.json"),
            ]
        )
        == 7
    )
    result = json.loads((project / "session-result.json").read_text())
    with pytest.raises((URLError, OSError)):
        urlopen(result["url"] + "/health", timeout=0.5)
    assert "stale-secret" not in capsys.readouterr().err
    assert not (project / ".claude").exists()
    # Only the installed library: start writes no project configuration.
    assert sorted(p.name for p in (project / ".clauz3").iterdir()) == [
        ".gitignore",
        "tools",
    ]


def test_missing_agent_and_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("clauz3.sessions.launcher.shutil.which", lambda name: None)
    with pytest.raises(launcher.StartError, match="not on PATH"):
        launcher.start_session(agent="claude")
    monkeypatch.setattr(
        "clauz3.sessions.launcher.shutil.which", lambda name: "/unused/claude"
    )
    with pytest.raises(launcher.StartError, match="no installed tools"):
        launcher.start_session(agent="claude")
    assert main(["start", "claude", "--port", "65536"]) == 2
    assert main(["start", "codex"]) == 2  # Explicit unrestricted opt-in required.


@pytest.mark.parametrize(
    "version, accepted",
    [
        ("2.1.283 (Claude Code)", True),
        ("2.1.284 (Claude Code)", True),
        ("2.2.0 (Claude Code)", True),
        ("2.1.282 (Claude Code)", False),
        ("2.1.99 (Claude Code)", False),
        ("2.1.283-beta (Claude Code)", False),
        ("unrecognized version", False),
    ],
)
def test_claude_version_check(tmp_path: Path, version: str, accepted: bool) -> None:
    executable = tmp_path / "claude"
    executable.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "assert sys.argv[1:] == ['--version']\n"
        f"print({version!r})\n"
    )
    executable.chmod(0o700)
    if accepted:
        launcher._check_claude_version(str(executable))
    else:
        with pytest.raises(launcher.StartError, match="2.1.283 or newer"):
            launcher._check_claude_version(str(executable))


@pytest.mark.parametrize("failure", ["timeout", "missing", "nonzero"])
def test_claude_version_probe_fails_closed(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def fail(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired("claude --version", 15)
        if failure == "missing":
            raise FileNotFoundError("claude disappeared")
        return subprocess.CompletedProcess([], 1, "2.1.283 (Claude Code)", "")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(launcher.StartError, match="2.1.283 or newer"):
        launcher._check_claude_version("claude")


def test_old_claude_refused_before_starting_service(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_agent(tmp_path, monkeypatch, "raise AssertionError('must not launch')\n")
    executable = tmp_path / "claude"
    executable.write_text(
        executable.read_text().replace("2.1.283 (Claude Code)", "2.1.282 (Claude Code)")
    )
    assert main(["start", "claude", "--no-browser"]) == 2
    output = capsys.readouterr().err
    assert "Update Claude Code" in output
    assert "Approval service:" not in output
    assert not (tmp_path / "clauz3-home/sessions").exists()


@pytest.mark.parametrize(
    "probe, message",
    [
        (
            "raise ImportError('FastMCP is unavailable in MCP SDK 2')",
            "FastMCP is unavailable",
        ),
        ("print('not JSON')", "invalid output"),
        ("print('[\"tools\"]')", "missing the expected session tools"),
        ("print('[1, \"tools\"]')", "missing the expected session tools"),
    ],
)
def test_broken_bridge_refuses_to_start_agent(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    probe: str,
    message: str,
) -> None:
    fake_agent(tmp_path, monkeypatch, "raise AssertionError('must not launch')\n")
    monkeypatch.setattr(
        launcher, "_bridge_command", lambda cwd, roots: [sys.executable, "-c", probe]
    )
    assert main(["start", "claude", "--no-browser"]) == 2
    error = capsys.readouterr().err
    assert message in error
    assert "Approval service:" not in error


def test_bridge_probe_timeout_does_not_launch_agent(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_agent(tmp_path, monkeypatch, "raise AssertionError('must not launch')\n")
    # Exercise the bridge deadline, independently of the CLI version probe.
    monkeypatch.setattr(launcher, "_check_claude_version", lambda executable: None)
    monkeypatch.setattr(launcher, "STARTUP_TIMEOUT", 0.1)
    monkeypatch.setattr(
        launcher,
        "_bridge_command",
        lambda cwd, roots: [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    assert main(["start", "claude", "--no-browser"]) == 2
    assert "MCP bridge startup check timed out" in capsys.readouterr().err


def test_session_bridge_receives_nested_domain(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An org-namespaced install reaches the session bridge as a trusted root."""
    nested = project / ".clauz3/tools/acme/lims/trusted"
    nested.mkdir(parents=True)
    (nested / "effects.py").write_text(
        "import clauz3\n\n\n@clauz3.trusted\ndef pipette(plate: str) -> None:\n"
        "    pass\n"
    )
    fake_agent(tmp_path, monkeypatch, "raise AssertionError('must not launch')\n")
    seen: list[list[Path]] = []

    def bridge(cwd: Path, roots: list[Path]) -> list[str]:
        seen.append(roots)
        return [sys.executable, "-c", "print('not JSON')"]

    monkeypatch.setattr(launcher, "_bridge_command", bridge)
    assert main(["start", "claude", "--no-browser"]) == 2
    assert seen == [
        [
            project.resolve() / ".clauz3/tools/acme/lims/trusted",
            project.resolve() / ".clauz3/tools/email/trusted",
        ]
    ]


@pytest.mark.parametrize("choice", ["1", "4"])
def test_start_first_run_chooser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    choice: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "store"))
    fake_agent(tmp_path, monkeypatch, "import sys\nsys.exit(0)\n")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: choice)
    assert main(["start", "claude", "--no-browser"]) == 0
    assert installed_domains(tmp_path) == ({"filesystem"} if choice == "1" else set())


def test_start_cancel_does_not_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    fake_agent(tmp_path, monkeypatch, "raise AssertionError('must not launch')\n")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "q")
    assert main(["start", "claude", "--no-browser"]) == 0
    assert not (tmp_path / ".clauz3").exists()


def test_occupied_port_does_not_launch_agent(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent(tmp_path, monkeypatch, "raise AssertionError('must not launch')\n")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        assert (
            main(
                [
                    "start",
                    "claude",
                    "--no-browser",
                    "--port",
                    str(listener.getsockname()[1]),
                ]
            )
            == 2
        )


@pytest.mark.parametrize(
    "signum, expected", [(signal.SIGINT, 130), (signal.SIGTERM, 143)]
)
def test_interrupt_cleans_up(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signum: int,
    expected: int,
) -> None:
    fake_agent(
        tmp_path,
        monkeypatch,
        f"""
import os, signal, time
os.kill(os.getppid(), {signum})
time.sleep(30)
""",
    )
    processes: list[subprocess.Popen[bytes]] = []
    original = subprocess.Popen

    def record(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("clauz3.sessions.launcher.subprocess.Popen", record)
    assert main(["start", "claude", "--no-browser"]) == expected
    assert len(processes) == 4  # version/bridge probes, approval service, agent
    assert all(p.poll() is not None for p in processes)


def test_readiness_failure_cleans_up(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent(tmp_path, monkeypatch, "raise AssertionError('must not launch')\n")
    processes: list[subprocess.Popen[bytes]] = []

    def fail(process: subprocess.Popen[bytes], url: str) -> None:
        processes.append(process)
        raise launcher.StartError("readiness failed")

    monkeypatch.setattr(launcher, "_wait_ready", fail)
    assert main(["start", "claude", "--no-browser"]) == 2
    assert processes[0].poll() is not None


def test_unrestricted_is_explicit() -> None:
    command = launcher._claude_command(
        "claude",
        cwd=Path.cwd(),
        roots=[],
        env={},
        model=None,
        prompt=None,
        unrestricted=True,
    )
    assert "--tools" not in command
    assert "--restricted" not in command
    assert "--disable-slash-commands" not in command
    assert command[command.index("--permission-mode") + 1] == "default"
    assert "--dangerously-skip-permissions" not in command
    assert (
        "you may also use native tools directly"
        in command[command.index("--append-system-prompt") + 1]
    )


def test_service_death_stops_agent(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent(tmp_path, monkeypatch, "import time\ntime.sleep(30)\n")
    processes: list[subprocess.Popen[bytes]] = []
    original = subprocess.Popen
    ready = launcher._wait_ready

    def record(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    def die(process: subprocess.Popen[bytes], url: str) -> None:
        ready(process, url)
        process.terminate()
        process.wait(timeout=5)

    monkeypatch.setattr("clauz3.sessions.launcher.subprocess.Popen", record)
    monkeypatch.setattr(launcher, "_wait_ready", die)
    assert main(["start", "claude", "--no-browser"]) == 2
    assert len(processes) == 4  # version/bridge probes, approval service, agent
    assert all(p.poll() is not None for p in processes)


def test_browser_failure_still_launches(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_agent(tmp_path, monkeypatch, "import sys\nsys.exit(0)\n")

    def fail(url: str) -> bool:
        assert "/ui/login?key=" in url
        raise OSError("no browser")

    monkeypatch.setattr("clauz3.sessions.launcher.webbrowser.open", fail)
    assert main(["start", "claude"]) == 0
    assert "Open the approver URL" in capsys.readouterr().err


def test_default_session_accepts_browser_approval(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_CASE", str(EMAIL / "cases/only_bob_pass.py"))
    monkeypatch.setenv("CLAUZ3_PROOF_TIMEOUT_SECONDS", "20")
    # Upper bounds only; a passing run returns in a few seconds. The subprocess
    # must outlast interpreter start-up, the proof and the approval wait, which
    # together exceeded the old 15s on a loaded machine.
    fake_agent(
        tmp_path,
        monkeypatch,
        """
import os, subprocess, sys
from pathlib import Path
result = subprocess.run(
    [sys.executable, "-P", "-m", "clauz3", "run", "-", "--approval-timeout", "60"],
    input=Path(os.environ["TEST_CASE"]).read_text(), text=True, capture_output=True,
    timeout=120,
)
assert result.returncode == 0, result.stdout + result.stderr
assert "approval: approved_once receipt=clz2." in result.stdout, result.stdout
""",
    )

    def approve(url: str) -> None:
        browser = build_opener(HTTPCookieProcessor(CookieJar()))
        with browser.open(url, timeout=2) as login:
            assert login.status == 200
            assert login.url.endswith("/ui")
        base = url.split("/ui/login")[0]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            with browser.open(base + "/requests", timeout=2) as response:
                requests = json.load(response)
            if requests:
                request_id = requests[0]["request_id"]
                request = Request(
                    f"{base}/requests/{request_id}/decision",
                    data=json.dumps({"decision": "approved_once"}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with browser.open(request, timeout=2) as response:
                    assert json.load(response)["decision"] == "approved_once"
                return
            time.sleep(0.05)
        raise AssertionError("no approval request received")

    with ThreadPoolExecutor(max_workers=1) as executor:
        approvals: list[Future[None]] = []

        def open_browser(url: str) -> bool:
            approvals.append(executor.submit(approve, url))
            return True

        monkeypatch.setattr("clauz3.sessions.launcher.webbrowser.open", open_browser)
        assert main(["start", "claude"]) == 0
        assert len(approvals) == 1
        approvals[0].result(timeout=1)


def test_resume_recreates_service_and_preserves_selection(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from clauz3.sessions.records import load_session

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "SKILL.md").write_text(
        "---\ndescription: Help with email\n---\nUseful text"
    )
    fake_agent(
        tmp_path,
        monkeypatch,
        """
import json, os, sys
from pathlib import Path
from clauz3.mcp import MCPClient
args = sys.argv
config = json.loads(args[args.index('--mcp-config') + 1])['mcpServers']['clauz3']
with MCPClient(command=config['command'], args=config['args'],
               env={**os.environ, **config['env']}) as client:
    assert len(client.list_tools()) == 6
    assert 'Help with email' in json.dumps(client.call('list_skills'))
result = {
 'id': args[args.index('--resume' if '--resume' in args else '--session-id') + 1],
 'resumed': '--resume' in args,
 'token': os.environ['CLAUZ3_APPROVAL_TOKEN'],
 'verify': os.environ['CLAUZ3_APPROVAL_VERIFY_KEY'],
 'url': os.environ['CLAUZ3_APPROVAL_SERVICE'],
}
Path('launch-result.json').write_text(json.dumps(result))
""",
    )
    assert main(["start", "claude", "--no-browser", "--skills-dir", str(docs)]) == 0
    initial = json.loads((project / "launch-result.json").read_text())
    assert initial["resumed"] is False
    record = load_session(initial["id"])
    assert record.cwd == str(project.resolve())
    metadata = (tmp_path / "clauz3-home/sessions" / f"{initial['id']}.json").read_text()
    assert initial["token"] not in metadata
    assert initial["verify"] not in metadata
    assert initial["url"] not in metadata
    monkeypatch.chdir(tmp_path)
    assert main(["resume", initial["id"], "--no-browser"]) == 0
    assert Path.cwd() == tmp_path
    resumed = json.loads((project / "launch-result.json").read_text())
    assert resumed["id"] == initial["id"]
    assert resumed["resumed"] is True
    assert resumed["token"] != initial["token"]
    assert resumed["verify"] != initial["verify"]
    for result in (initial, resumed):
        with pytest.raises((URLError, OSError)):
            urlopen(result["url"] + "/health", timeout=0.5)
    output = capsys.readouterr().err
    assert output.count("Approval service stopped.") == 2
    assert f"clauz3 resume {initial['id']}" in output


@pytest.mark.parametrize("changed", ["libraries", "policy", "skill documents"])
def test_resume_refuses_changed_selection(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    changed: str,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "SKILL.md").write_text("Original")
    policy = tmp_path / "policy.json"
    policy.write_text('{"version":1,"rules":[]}')
    fake_agent(tmp_path, monkeypatch, "pass\n")
    assert (
        main(
            [
                "start",
                "claude",
                "--no-browser",
                "--policy",
                str(policy),
                "--skills-dir",
                str(docs),
            ]
        )
        == 0
    )
    session_id = next((tmp_path / "clauz3-home/sessions").glob("*.json")).stem
    if changed == "libraries":
        (project / ".clauz3/tools/email/trusted/extra.py").write_text("# added\n")
    elif changed == "policy":
        policy.write_text('{"version": 1, "rules": []}\n')
    else:
        (docs / "SKILL.md").write_text("Replaced")
    capsys.readouterr()
    assert main(["resume", session_id, "--no-browser"]) == 2
    error = capsys.readouterr().err
    assert changed in error and "changed" in error
    assert "Approval service:" not in error


def test_resume_metadata_and_lease_fail_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from uuid import uuid4

    from clauz3.sessions.records import session_lease

    assert main(["resume", "../../outside"]) == 2
    session_id = str(uuid4())
    assert main(["resume", session_id]) == 2
    assert "unknown clauz3 session" in capsys.readouterr().err
    with (
        session_lease(session_id),
        pytest.raises(ValueError, match="already running"),
        session_lease(session_id),
    ):
        pytest.fail("duplicate session acquired lease")
    record = tmp_path / "clauz3-home/sessions" / f"{session_id}.json"
    record.write_text('{"version": 1, "agent": "arbitrary-command"}')
    assert main(["resume", session_id]) == 2
    assert "metadata" in capsys.readouterr().err


def test_resume_fingerprint_tracks_linked_root_sources(tmp_path: Path) -> None:
    from clauz3.sessions.records import selection_fingerprints

    project = tmp_path / "project"
    domain = project / "tools/example"
    domain.mkdir(parents=True)
    target = project / "selected-source"
    target.mkdir()
    source = target / "effects.py"
    source.write_text("# original")
    root = domain / "trusted"
    root.symlink_to(target, target_is_directory=True)
    before = selection_fingerprints(project, [root], None, ())
    source.write_text("# changed")
    assert selection_fingerprints(project, [root], None, ()) != before
    outside = tmp_path / "external"
    outside.mkdir()
    root.unlink()
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="root escapes project"):
        selection_fingerprints(project, [root], None, ())


def test_empty_skill_selection_warns_and_still_starts(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    docs = tmp_path / "empty-skills"
    docs.mkdir()
    fake_agent(tmp_path, monkeypatch, "raise SystemExit(0)")
    assert main(["start", "claude", "--no-browser", "--skills-dir", str(docs)]) == 0
    output = capsys.readouterr().err
    assert "Selected skill documents: 0" in output
    assert "Warning: no SKILL.md documents found" in output
    assert "Approval service stopped." in output


@pytest.mark.parametrize("problem", ["outside", "dangling", "unreadable"])
def test_start_refuses_unfingerprintable_python_outside_trusted_root(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    problem: str,
) -> None:
    fake_agent(tmp_path, monkeypatch, "raise AssertionError('must not launch')")
    notes = project / ".clauz3/tools/notes.py"
    if problem == "outside":
        outside = tmp_path / "outside.py"
        outside.write_text("# external")
        notes.symlink_to(outside)
    elif problem == "dangling":
        notes.symlink_to(project / "missing.py")
    else:
        notes.write_text("# unreadable")
        read_bytes = Path.read_bytes

        def refuse_notes(path: Path) -> bytes:
            if path == notes:
                raise PermissionError(f"Permission denied: {path}")
            return read_bytes(path)

        # Portable even when the suite runs as root.
        monkeypatch.setattr(Path, "read_bytes", refuse_notes)
    assert main(["start", "claude", "--no-browser"]) == 2
    output = capsys.readouterr().err
    assert "Approval service:" not in output
    assert "notes.py" in output


@pytest.mark.parametrize("value", ["bad", "0", "-1", "nan", "inf"])
def test_invalid_proof_budget_is_reported_before_startup(
    monkeypatch: pytest.MonkeyPatch, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUZ3_PROOF_TIMEOUT_SECONDS", value)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid configuration started a subprocess")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert main(["start", "claude", "--no-browser"]) == 2
    error = capsys.readouterr().err
    assert "CLAUZ3_PROOF_TIMEOUT_SECONDS must be a finite positive number" in error
    assert "Traceback" not in error


@pytest.mark.parametrize("empty", [False, True])
def test_ui_launches_workspace_and_cleans_up(
    empty: bool,
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_agent(
        tmp_path,
        monkeypatch,
        "import runpy, sys\n"
        "runpy.run_path("
        + repr(str(Path(__file__).parent / "fixtures/workspace_agent.py"))
        + ', run_name="__main__")\n',
        version="0.89.1",
    )
    if empty:
        shutil.rmtree(project / ".clauz3/tools")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    opened: list[str] = []

    def browser(url: str) -> bool:
        opened.append(url)
        from websockets.sync.client import connect
        from websockets.typing import Origin

        cookies = CookieJar()
        opener = build_opener(HTTPCookieProcessor(cookies))
        with opener.open(url, timeout=5) as response:
            page = response.read().decode()
        assert "Message agent" in page
        assert "workspace.js" in page
        assert "?key=" not in page
        origin = url.split("/ui/login")[0]
        with connect(
            origin.replace("http://", "ws://") + "/api/chat/acp",
            origin=Origin(origin),
            additional_headers={
                "Cookie": "; ".join(f"{c.name}={c.value}" for c in cookies)
            },
        ) as ws:
            for _ in range(30):
                message = json.loads(ws.recv(timeout=10))
                if (
                    message["method"] == "clauz3/state"
                    and message["params"]["sessionId"]
                ):
                    assert message["params"]["cwd"] == str(project.resolve())
                    assert message["params"]["libraries"] == (
                        [] if empty else ["email"]
                    )
                    break
            else:
                raise AssertionError(
                    "Launcher-created workspace did not start the fixture adapter"
                )
            ws.send(
                json.dumps({"method": "session/prompt", "params": {"prompt": "hello"}})
            )
            output = []
            for _ in range(30):
                message = json.loads(ws.recv(timeout=10))
                output.append(message)
                if message["method"] == "clauz3/turn_end":
                    break
            else:
                raise AssertionError("Launched conversation did not finish a turn")
            assert "Hello " in json.dumps(output)
        raise KeyboardInterrupt

    monkeypatch.setattr("clauz3.sessions.launcher.webbrowser.open", browser)
    assert main(["ui", "claude"]) == 130
    assert len(opened) == 1
    with pytest.raises((URLError, OSError)):
        urlopen(opened[0].split("/ui/login")[0] + "/health", timeout=0.5)
    diagnostics = capsys.readouterr().err
    assert "Resume with:" not in diagnostics
    assert "/mcp" not in diagnostics
    assert "slash commands are disabled" in diagnostics


def test_ui_refuses_unreviewed_adapter(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_agent(
        tmp_path,
        monkeypatch,
        "raise AssertionError('must not launch')",
        version="9.9.9",
    )
    assert main(["ui", "--no-browser"]) == 2
    assert "UI requires claude-agent-acp 0.89.1" in capsys.readouterr().err


def test_ui_project_lease_conflict_is_actionable_and_does_not_create_history(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from clauz3.libraries.discovery import global_library_root
    from clauz3.sessions.history import ConversationStore

    fake_agent(
        tmp_path,
        monkeypatch,
        "raise AssertionError('must not launch')",
        version="0.89.1",
    )
    store = ConversationStore(
        global_library_root() / "conversations.sqlite", str(project)
    )
    with store.lease():
        assert main(["ui", "--no-browser"]) == 2
    assert "This project already has a running workspace UI" in capsys.readouterr().err
    assert store.list() == []
