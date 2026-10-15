"""Service startup grants come from the operator's explicit root flags."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from clauz3.approvals.authority import ApprovedLayer
from clauz3.cli import main


def _policy(tmp_path: Path) -> Path:
    policy = tmp_path / "approval-policy.json"
    policy.write_text('{"imports": {}, "rules": []}')
    return policy


def test_policy_service_requires_explicit_operator_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    # Discovery is useful to a local runner but must not silently grant the
    # service loading authority over a project in its working directory.
    trusted = tmp_path / "tools/email/trusted"
    trusted.mkdir(parents=True)
    marker = tmp_path / "initialized"
    (trusted / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    serve = Mock()
    monkeypatch.setattr("clauz3.cli.serve_approval_service", serve)

    assert main(["approval-service", "--policy", str(_policy(tmp_path))]) == 2
    assert "--policy requires operator-configured roots" in capsys.readouterr().err
    serve.assert_not_called()
    assert not marker.exists()


@pytest.mark.parametrize("options", ["repeated", "plural", "trusted", "import"])
def test_policy_service_configures_one_ordered_canonical_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUZ3_APPROVAL_TOKEN", "operator-token")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    serve = Mock()
    monkeypatch.setattr("clauz3.cli.serve_approval_service", serve)
    if options == "repeated":
        flags = [
            "--trusted-root",
            "second",
            "--trusted-root",
            "first",
            "--import-root",
            "second",
            "--import-root",
            ".",
        ]
    elif options == "plural":
        flags = [
            "--trusted-roots",
            "second",
            "first",
            "--import-roots",
            "second",
            ".",
        ]
    elif options == "trusted":
        flags = ["--trusted-root", "second", "--trusted-root", "first"]
    else:
        flags = ["--import-root", "second", "--import-root", "."]

    assert (
        main(
            [
                "approval-service",
                "--policy",
                str(_policy(tmp_path)),
                *flags,
            ]
        )
        == 0
    )

    serve.assert_called_once()
    kwargs = serve.call_args.kwargs
    assert kwargs["authorized_layers"] == (
        ApprovedLayer(
            trusted_roots=() if options == "import" else (second, first),
            # Without --import-root the service authorizes the runner's
            # default selection: the working directory behind its .clauz3.
            import_roots=(tmp_path / ".clauz3", tmp_path)
            if options == "trusted"
            else (second, tmp_path),
        ),
    )
    assert kwargs["auth_token"] == "operator-token"
    assert kwargs["policy"] is not None


@pytest.mark.parametrize("no_auth", [False, True])
def test_manual_service_needs_no_layer_grant(
    monkeypatch: pytest.MonkeyPatch,
    no_auth: bool,
) -> None:
    monkeypatch.setenv("CLAUZ3_APPROVAL_TOKEN", "operator-token")
    serve = Mock()
    monkeypatch.setattr("clauz3.cli.serve_approval_service", serve)

    assert main(["approval-service", *(["--no-auth"] if no_auth else [])]) == 0

    serve.assert_called_once()
    kwargs = serve.call_args.kwargs
    assert kwargs["authorized_layers"] == ()
    assert kwargs["policy"] is None
    assert kwargs["auth_token"] == (None if no_auth else "operator-token")
    if no_auth:
        assert kwargs["decision_token"] is None
    else:
        assert kwargs["decision_token"]
        assert kwargs["decision_token"] != "operator-token"


def test_decision_token_equal_to_submission_token_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUZ3_APPROVAL_TOKEN", "operator-token")
    monkeypatch.setenv("CLAUZ3_APPROVAL_DECISION_TOKEN", "operator-token")
    serve = Mock()
    monkeypatch.setattr("clauz3.cli.serve_approval_service", serve)

    assert main(["approval-service"]) == 2
    serve.assert_not_called()
