"""Mock declarations are descriptive; trusted effects still require proof/approval."""

import ast
from pathlib import Path

import astroid
import pytest
from fastapi.testclient import TestClient

import clauz3
from clauz3._vendor.deal_solver import Conclusion, UnsupportedError
from clauz3.approvals.authority import ApprovedLayer
from clauz3.approvals.service import create_approval_app
from clauz3.decorators import import_aliases, is_trusted_effect
from clauz3.libraries.mock_metadata import mock_declarations
from clauz3.libraries.workspace import WorkspaceLibraries
from clauz3.prover import (
    AgentTheorem,
    ProverConfigError,
    prove_text,
    validate_untrusted_source,
)
from clauz3.sessions.catalogue import snapshot_catalogue


@pytest.mark.parametrize("called", [False, True])
def test_mock_is_identity_not_enforcement(called: bool) -> None:
    calls = []

    def action(value: int) -> int:
        calls.append(value)
        return value + 1

    decorator = clauz3.mock() if called else clauz3.mock
    marked = decorator(action)
    assert marked is action
    assert marked(3) == 4
    assert calls == [3]
    with pytest.raises(TypeError, match="takes no arguments"):
        clauz3.mock("no")  # type: ignore[call-overload]


@pytest.mark.parametrize(
    ("imports", "marker"),
    [
        ("import clauz3", "clauz3.mock"),
        ("import clauz3 as cz", "cz.mock()"),
        ("from clauz3 import mock as simulated", "simulated"),
        ("from clauz3.effects import mock as simulated", "simulated()"),
        ("import deal as d\nimport clauz3", "d.chain(clauz3.mock)"),
    ],
)
def test_discovery_reads_declarations_without_importing(
    tmp_path: Path, imports: str, marker: str
) -> None:
    root = tmp_path / "tools/example/trusted"
    root.mkdir(parents=True)
    (root / "effects.py").write_text(
        "import clauz3\n" + imports + "\nraise RuntimeError('must not import')\n"
        "@clauz3.trusted\n@" + marker + "\ndef send(x: str) -> None: pass\n"
    )
    summary = mock_declarations(tmp_path, [root])
    assert summary["kind"] == "mock"
    assert summary["declared_mock"] == ["tools/example/trusted/effects.py:send"]
    api = "\n".join(snapshot_catalogue(tmp_path, [root])["api"])
    assert "Declared mock:" in api
    assert "does not enforce isolation" in api


def test_mock_does_not_grant_trusted_status(tmp_path: Path) -> None:
    source = "import clauz3\n@clauz3.mock\ndef main() -> None: pass\n"
    module = ast.parse(source)
    function = module.body[1]
    assert isinstance(function, ast.FunctionDef)
    assert not is_trusted_effect(function, import_aliases(module))
    with pytest.raises(ProverConfigError, match="not a clauz3 guarantee"):
        validate_untrusted_source(source=source, path=tmp_path / "entry.py")
    root = tmp_path / "trusted"
    root.mkdir()
    (root / "helpers.py").write_text(source)
    assert mock_declarations(tmp_path, [root])["declared_mock"] == []


@pytest.mark.parametrize(
    "marker", ["clauz3.mock(1)", "clauz3.mock(value=1)", "fake.mock"]
)
def test_unknown_or_invalid_mock_spelling_is_unmarked(
    tmp_path: Path, marker: str
) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    (root / "effects.py").write_text(
        "import clauz3\nimport json as fake\n@clauz3.trusted\n@"
        + marker
        + "\ndef send() -> None: pass\n"
    )
    summary = mock_declarations(tmp_path, [root])
    assert summary["kind"] == "unmarked"
    assert not summary["declared_mock"]


def test_mixed_and_unreadable_sources_are_not_labelled_all_mock(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    (root / "effects.py").write_text(
        "import clauz3\n@clauz3.trusted\n@clauz3.mock\n"
        "def fake() -> None: pass\n@clauz3.trusted\ndef live() -> None: pass\n"
    )
    assert mock_declarations(tmp_path, [root])["kind"] == "mixed"
    (root / "broken.py").write_text("def invalid(")
    summary = mock_declarations(tmp_path, [root])
    assert summary["kind"] == "unknown"
    assert summary["errors"]


def test_symlink_is_not_read_for_mock_metadata(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text(
        "import clauz3\n@clauz3.trusted\n@clauz3.mock\ndef fake(): pass\n"
    )
    (root / "effects.py").symlink_to(outside)
    summary = mock_declarations(tmp_path, [root])
    assert summary["kind"] == "unknown"
    assert not summary["declared_mock"]


@pytest.mark.parametrize(
    ("limit", "value", "expected"),
    [(1, 2, Conclusion.OK), (0, 2, Conclusion.FAIL), (1, -1, Conclusion.FAIL)],
)
def test_mock_effects_still_have_facts_and_preconditions(
    tmp_path: Path, limit: int, value: int, expected: Conclusion
) -> None:
    root = tmp_path / "mock_test_layer"
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "effects.py").write_text(
        "import clauz3\n@clauz3.requires(lambda value: value >= 0)\n"
        "@clauz3.mock\n@clauz3.trusted\ndef send(value: int) -> None: pass\n"
    )
    (root / "contracts.py").write_text(
        "from clauz3.spec import contract, effect, ContractSpec\n"
        "from .effects import send\n@contract\ndef at_most(n: int) -> ContractSpec:\n"
        "    return effect(send).count() <= n\n"
    )
    source = (
        "import clauz3\nfrom mock_test_layer.effects import send\n"
        "from mock_test_layer.contracts import at_most\n"
        f"@clauz3.guarantee(at_most({limit}))\ndef main() -> None:\n    send({value})\n"
    )
    result = prove_text(source, trusted_roots=[root], import_paths=[tmp_path])
    assert result[0].proof.conclusion == expected


def test_workspace_reports_installed_and_staged_declarations(tmp_path: Path) -> None:
    project = tmp_path / "project"
    root = project / "tools/custom/trusted"
    root.mkdir(parents=True)
    (root / "effects.py").write_text(
        "import clauz3\n@clauz3.mock\n@clauz3.trusted\ndef action(): pass\n"
    )
    manager = WorkspaceLibraries(project, tmp_path / "state", ["custom"])
    assert manager.snapshot()["mockDeclarations"]["custom"]["kind"] == "mock"
    review = manager.prepare("remove", "", "custom")
    assert review["mockDeclarations"]["kind"] == "mock"
    manager.discard()
    review = manager.prepare("install", "stdlib:filesystem", "")
    assert review["mockDeclarations"]["kind"] == "unmarked"
    manager.discard()


@pytest.mark.parametrize("authorized", [False, True])
def test_approval_labels_are_operator_sourced_and_do_not_approve(
    tmp_path: Path, authorized: bool
) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    (root / "effects.py").write_text(
        "import clauz3\nraise RuntimeError('UI must not import')\n"
        "@clauz3.mock\n@clauz3.trusted\ndef send(): pass\n"
    )
    layer = ApprovedLayer((root,), (tmp_path,))
    with TestClient(
        create_approval_app(
            authorized_layers=[layer] if authorized else [], max_wait_seconds=0.01
        )
    ) as client:
        response = client.post(
            "/api/requests",
            json={
                "request_id": "mock_test",
                "trusted_roots": [str(root)],
                "import_roots": [str(tmp_path)],
                "library_mock_declarations": {
                    "kind": "mock",
                    "declared_mock": ["spoofed"],
                },
            },
        )
        assert response.status_code == 503  # Still awaits the operator's decision.
        record = client.get("/requests/mock_test").json()
        assert record["status"] == "pending"
        assert "approval" not in record
        page = client.get("/ui/requests/mock_test").text
        assert "spoofed" not in page
        assert ("Includes simulated tools" in page) is authorized
        if authorized:
            assert record["library_mock_declarations"]["kind"] == "mock"
            assert record["library_mock_declarations"]["declared_mock"] == [
                "trusted/effects.py:send"
            ]
            assert "not enforced isolation" in page
        else:
            assert "library_mock_declarations" not in record


@pytest.mark.parametrize("domain", ["my.lib", "2fa", ".archive"])
def test_unmanageable_domain_does_not_break_library_snapshot(
    tmp_path: Path, domain: str
) -> None:
    (tmp_path / "tools" / domain / "trusted").mkdir(parents=True)
    valid = tmp_path / "tools/valid/trusted"
    valid.mkdir(parents=True)
    (valid / "effects.py").write_text(
        "import clauz3\n@clauz3.mock\n@clauz3.trusted\ndef action(): pass\n"
    )
    manager = WorkspaceLibraries(tmp_path, tmp_path / "state", [])
    snapshot = manager.snapshot()
    assert snapshot["mockDeclarations"][domain]["kind"] == "unknown"
    assert snapshot["mockDeclarations"][domain]["errors"]
    assert snapshot["mockDeclarations"]["valid"]["kind"] == "mock"
    with pytest.raises(ValueError):
        manager.select([domain])


def test_symlink_domain_does_not_break_snapshot(tmp_path: Path) -> None:
    (tmp_path / "outside/trusted").mkdir(parents=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/shared").symlink_to(
        tmp_path / "outside", target_is_directory=True
    )
    manager = WorkspaceLibraries(tmp_path, tmp_path / "state", [])
    # Some Python versions' glob doesn't discover symlinked directories. Pin
    # the previously accepted installed-domain input independently of glob.
    from unittest.mock import patch

    with patch.object(manager, "installed", return_value=["shared"]):
        assert manager.snapshot()["mockDeclarations"]["shared"]["kind"] == "unknown"
    with pytest.raises(ValueError, match="Symbolic links"):
        manager.select(["shared"])


@pytest.mark.parametrize("wrapper", ["if True:", "try:", "with object():"])
def test_module_control_flow_counts_unmarked_effects(
    tmp_path: Path, wrapper: str
) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    source = (
        "import clauz3\n@clauz3.trusted\n@clauz3.mock\ndef fake(): pass\n"
        + wrapper
        + "\n    @clauz3.trusted\n    def live(): pass\n"
    )
    if wrapper == "try:":
        source += "except Exception:\n    pass\n"
    (root / "effects.py").write_text(source)
    summary = mock_declarations(tmp_path, [root])
    assert summary["kind"] == "mixed"
    assert summary["unmarked"] == ["trusted/effects.py:live"]
    assert mock_declarations(None, [root])["unmarked"] == [f"{root}/effects.py:live"]


def test_card_discloses_simulated_tools_and_inspection_errors() -> None:
    from clauz3.approvals.service import _render_mock_declarations

    rendered = _render_mock_declarations(
        {
            "kind": "unknown",
            "declared_mock": ["fake"],
            "unmarked": ["live"],
            "errors": ["<script>bad</script>"],
        }
    )
    assert "Includes simulated tools" in rendered
    assert "Unmarked effects" not in rendered
    assert '<details data-panel="mock-effects">' in rendered
    assert "fake" in rendered
    assert "Could not inspect all sources" in rendered
    assert "&lt;script&gt;bad&lt;/script&gt;" in rendered
    assert "<script>" not in rendered


@pytest.mark.parametrize("marker", ["clauz3.mock(1)", "clauz3.mock(value=1)"])
def test_invalid_mock_decorator_is_not_proved(tmp_path: Path, marker: str) -> None:
    root = tmp_path / "invalid_mock_layer"
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "effects.py").write_text(
        "import clauz3\n@clauz3.trusted\n@"
        + marker
        + "\ndef send(value: int) -> None: pass\n"
    )
    (root / "contracts.py").write_text(
        "from clauz3.spec import contract, effect, ContractSpec\n"
        "@contract\ndef at_most(n: int) -> ContractSpec:\n"
        "    return effect().count() <= n\n"
    )
    source = (
        "import clauz3\nfrom invalid_mock_layer.effects import send\n"
        "from invalid_mock_layer.contracts import at_most\n"
        "@clauz3.guarantee(at_most(1))\ndef main() -> None:\n    send(1)\n"
    )
    # The invalid runtime call prevents importing the trusted layer at all.
    with pytest.raises(TypeError):
        prove_text(source, trusted_roots=[root], import_paths=[tmp_path])
    # Also pin the static declaration reader independently of that import error.
    module = astroid.parse((root / "effects.py").read_text())
    function = module.body[1]
    assert isinstance(function, astroid.FunctionDef)
    with pytest.raises(UnsupportedError, match="unsupported decorator"):
        list(AgentTheorem.get_contracts(function))


def test_card_omits_mock_notice_for_ordinary_libraries() -> None:
    from clauz3.approvals.service import _render_mock_declarations

    assert (
        _render_mock_declarations(
            {
                "kind": "unmarked",
                "declared_mock": [],
                "unmarked": ["read_file"],
                "errors": [],
            }
        )
        == ""
    )
