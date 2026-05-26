import json
from pathlib import Path
from typing import Any, cast

import pytest
from _pytest.capture import CaptureFixture

from clauz3.cli import main
from clauz3.config import DEFAULT_ALLOW, DEFAULT_MODE, ConfigError, configure_repo


def _settings(root: Path) -> dict[str, Any]:
    text = (root / ".claude/settings.json").read_text()
    return cast(dict[str, Any], json.loads(text))


def _write_settings(root: Path, text: str) -> Path:
    path = root / ".claude/settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_config_writes_default_settings(tmp_path: Path) -> None:
    result = configure_repo(into=tmp_path)

    assert result.created is True
    assert result.added == DEFAULT_ALLOW
    permissions = _settings(tmp_path)["permissions"]
    assert permissions["allow"] == DEFAULT_ALLOW
    assert permissions["defaultMode"] == DEFAULT_MODE


def test_config_is_idempotent(tmp_path: Path) -> None:
    configure_repo(into=tmp_path)
    result = configure_repo(into=tmp_path)

    assert result.created is False
    assert result.added == []
    assert _settings(tmp_path)["permissions"]["allow"] == DEFAULT_ALLOW


def test_config_merges_into_existing_allow(tmp_path: Path) -> None:
    _write_settings(tmp_path, json.dumps({"permissions": {"allow": ["Bash(git:*)"]}}))

    result = configure_repo(into=tmp_path)

    allow = _settings(tmp_path)["permissions"]["allow"]
    assert allow[0] == "Bash(git:*)"
    assert "Bash(clauz3:*)" in allow
    assert "Bash(clauz3:*)" in result.added
    assert "Bash(git:*)" not in result.added


def test_config_preserves_existing_default_mode(tmp_path: Path) -> None:
    _write_settings(
        tmp_path,
        json.dumps({"permissions": {"defaultMode": "acceptEdits", "allow": []}}),
    )

    configure_repo(into=tmp_path)

    assert _settings(tmp_path)["permissions"]["defaultMode"] == "acceptEdits"


def test_config_refuses_malformed_without_force(tmp_path: Path) -> None:
    _write_settings(tmp_path, "{not json")

    with pytest.raises(ConfigError, match="not valid JSON"):
        configure_repo(into=tmp_path)


def test_config_force_overwrites_malformed(tmp_path: Path) -> None:
    _write_settings(tmp_path, "{not json")

    result = configure_repo(into=tmp_path, force=True)

    assert result.created is False
    assert _settings(tmp_path)["permissions"]["allow"] == DEFAULT_ALLOW


def test_config_cli_writes_and_reports(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    result = main(["config", "--into", str(tmp_path)])

    assert result == 0
    assert "wrote claude config:" in capsys.readouterr().out
    assert (tmp_path / ".claude/settings.json").is_file()


def test_config_cli_idempotent_message(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    main(["config", "--into", str(tmp_path)])
    capsys.readouterr()

    result = main(["config", "--into", str(tmp_path)])

    assert result == 0
    assert "already up to date" in capsys.readouterr().out
