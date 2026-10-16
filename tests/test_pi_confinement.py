"""Fail-closed runtime selection and the actual Pi SDK's tool execution boundary."""

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from clauz3.sessions import providers
from clauz3.sessions.native_adapter import NativeAdapter, restricted_pi_environment


def test_restricted_pi_runtime_is_optional_and_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("clauz3.sessions.providers.shutil.which", lambda _: "/node")
    entry = tmp_path / "dist/cli.js"
    entry.parent.mkdir()
    entry.touch()
    (entry.parent / "index.js").touch()
    package = tmp_path / "package.json"
    for name, version, valid in [
        ("@earendil-works/pi-coding-agent", providers.PI_VERSION, True),
        ("@earendil-works/pi-coding-agent", "999.0.0", False),
        ("@mariozechner/pi-coding-agent", providers.PI_VERSION, False),
    ]:
        package.write_text(json.dumps({"name": name, "version": version}))
        if valid:
            providers.check_runtime("pi", [str(entry)], False)
        else:
            with pytest.raises(ValueError, match="Restricted Pi requires"):
                providers.check_runtime("pi", [str(entry)], False)
        providers.check_runtime("pi", [str(entry)], True)
    providers.check_mode("pi", False)


def test_restricted_pi_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "NODE_OPTIONS",
        "NODE_PATH",
        "PI_CODING_AGENT_DIR",
        "OPENAI_BASE_URL",
        "CLAUZ3_APPROVAL_SIGNING_KEY",
        "CLAUZ3_APPROVAL_DECISION_TOKEN",
    ):
        monkeypatch.setenv(name, "must-not-inherit")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "literal-test-key")
    environment = restricted_pi_environment()
    assert "must-not-inherit" not in environment.values()
    assert environment["ANTHROPIC_API_KEY"] == "literal-test-key"
    assert environment["PI_OFFLINE"] == "1"


@pytest.mark.parametrize("variable", ["CLAUZ3_HOME", "XDG_DATA_HOME"])
def test_restricted_pi_bridge_sees_the_same_library_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """The bridge under Pi inherits this environment; its libraries tool must
    resolve the same global root as under the other providers."""
    from clauz3.libraries.discovery import global_library_root

    monkeypatch.delenv("CLAUZ3_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv(variable, str(tmp_path))
    expected = global_library_root()
    assert expected.is_relative_to(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from clauz3.libraries.discovery import global_library_root; "
            "print(global_library_root())",
        ],
        env=restricted_pi_environment(),
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(result.stdout.strip()) == expected


def test_pi_rejects_credential_commands_before_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setattr("clauz3.sessions.native_adapter.pi_sdk", lambda _: tmp_path)
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                "test": {"type": "api_key", "key": "!touch escaped"},
            }
        )
    )

    async def check() -> None:
        adapter = NativeAdapter("pi", "unused")
        try:
            with pytest.raises(ValueError, match="credential commands are disabled"):
                await adapter.start(
                    {
                        "cwd": str(tmp_path),
                        "mcpServers": [
                            {
                                "name": "clauz3",
                                "command": "unused",
                                "args": [],
                                "env": [],
                            }
                        ],
                    },
                    resume=False,
                )
            assert adapter.process is None
        finally:
            await adapter.close()

    asyncio.run(check())


def test_pi_resume_cannot_change_confinement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clauz3.sessions.native_adapter import session_path

    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path))
    identifier = "11111111-1111-4111-8111-111111111111"
    session_path(identifier).write_text(
        json.dumps(
            {
                "agent": "pi",
                "cwd": str(tmp_path),
                "restricted": False,
                "native_id": "old",
            }
        )
    )

    async def check() -> None:
        adapter = NativeAdapter("pi", "unused")
        try:
            params: dict[str, Any] = {"sessionId": identifier, "cwd": str(tmp_path)}
            with pytest.raises(ValueError, match="change Pi confinement mode"):
                await adapter.start(params, resume=True)
            assert adapter.process is None
        finally:
            await adapter.close()

    asyncio.run(check())


def test_installed_pi_sdk_confinement(tmp_path: Path) -> None:
    executable = shutil.which("pi")
    if executable is None:
        pytest.skip("Optional pinned Pi runtime is not installed")
    try:
        sdk = providers.pi_sdk(executable)
    except ValueError:
        pytest.skip("Optional pinned Pi runtime is not installed")
    fixture = Path(__file__).with_name("fixtures") / "pi_confinement.mjs"
    result = subprocess.run(
        ["node", str(fixture), str(sdk), str(tmp_path)],
        env=restricted_pi_environment(),
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "confinement and resume verified" in result.stdout


def test_pi_reports_safe_model_error_through_native_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = shutil.which("pi")
    if executable is None:
        pytest.skip("Optional pinned Pi runtime is not installed")
    try:
        providers.pi_sdk(executable)
    except ValueError:
        pytest.skip("Optional pinned Pi runtime is not installed")
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))

    async def check() -> None:
        assert executable is not None
        adapter = NativeAdapter("pi", executable)
        try:
            with pytest.raises(
                ValueError, match="^Choose an exact Pi --model provider/model$"
            ):
                await adapter.start(
                    {
                        "cwd": str(tmp_path),
                        "_meta": {"clauz3": {"model": "missing/provider-model"}},
                        "mcpServers": [
                            {
                                "name": "clauz3",
                                "command": sys.executable,
                                "args": [
                                    "-P",
                                    "-m",
                                    "clauz3.sessions.tools",
                                    str(tmp_path),
                                ],
                                "env": [],
                            }
                        ],
                    },
                    resume=False,
                )
            assert not list((tmp_path / "home/native-sessions").glob("*.json"))
        finally:
            await adapter.close()

    asyncio.run(check())
