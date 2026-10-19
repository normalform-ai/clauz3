"""Approval identity comes from the selected registration, not agent aliases."""

import ast
import inspect
import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.approvals.descriptions import render_guarantees
from clauz3.contract_identity import identify_call
from clauz3.guarantees import contract_descriptions, contract_identities
from clauz3.prover import _selected_layer_context, prove_text
from clauz3.spec import ContractSpec, contract

ROOT = Path(__file__).resolve().parents[1]


def test_selected_identity_survives_aliases_without_description_templates(
    tmp_path: Path,
) -> None:
    snapshots = []
    for number, alias in enumerate(("rules", "anything")):
        project = tmp_path / str(number)
        shutil.copytree(ROOT / "examples/email/tools", project / "tools")
        root = project / "tools/email/trusted"
        contracts = root / "contracts.py"
        # Same namespace in two installations, with different reviewed code.
        contracts.write_text(contracts.read_text() + f"\n# installation {number}\n")
        source = f"""import clauz3
from tools.email.trusted import contracts as {alias}
from tools.email.trusted.effects import send_email

@clauz3.guarantee({alias}.at_most(1))
def main() -> None:
    send_email("bob@example.org", "hello")
"""
        with _selected_layer_context([root], [project]) as layer:
            proofs = prove_text(source, trusted_roots=[root], import_paths=[project])
            assert all(p.proof.conclusion == Conclusion.OK for p in proofs)
            assert not contract_descriptions(source=source, target="main")
            identities = contract_identities(
                source=source,
                target="main",
                roots=[root],
                sources=layer.profile_source_identity(),
            )
        identity = identities[f"{alias}.at_most(1)"]
        assert identity["symbol"] == "tools.email.trusted.contracts.at_most"
        assert identity["call"] == "email.at_most(count=1)"
        assert identity["source_file"] == str(contracts)
        assert identity["source_sha256"] == sha256(contracts.read_bytes()).hexdigest()
        lines = contracts.read_text().splitlines(keepends=True)
        assert "".join(lines[int(str(identity["source_line"])) - 1 :]).startswith(
            str(identity["source_code"])
        )
        assert identity["mock"] == "mock"
        snapshots.append(identity)
    assert snapshots[0]["source_sha256"] != snapshots[1]["source_sha256"]
    assert snapshots[0]["layer_sha256"] != snapshots[1]["layer_sha256"]


def test_nested_library_namespace_manifest_and_recorded_origin(tmp_path: Path) -> None:
    domain = tmp_path / "tools/google_workspace/gmail"
    shutil.copytree(
        ROOT / "src/clauz3/stdlib/google_workspace/tools/google_workspace/gmail",
        domain,
    )
    (domain / ".clauz3-origin.json").write_text(
        '{"source":"stdlib:google_workspace", "revision":"test-revision"}'
    )
    root = domain / "trusted"
    source = """import clauz3
from tools.google_workspace.gmail.trusted.contracts import no_emails as safe

@clauz3.guarantee(safe())
def main() -> None:
    pass
"""
    with _selected_layer_context([root], [tmp_path]) as layer:
        proofs = prove_text(source, trusted_roots=[root], import_paths=[tmp_path])
        assert all(p.proof.conclusion == Conclusion.OK for p in proofs)
        identities = contract_identities(
            source=source,
            target="main",
            roots=[root],
            sources=layer.profile_source_identity(),
        )
    identity = identities["safe()"]
    assert identity["call"] == "google_workspace.gmail.no_emails()"
    assert identity["library"] == "google_workspace.gmail"
    assert identity["title"] == "Gmail"
    assert identity["origin"] == "stdlib:google_workspace"
    assert identity["revision"] == "test-revision"
    assert (
        identity["symbol"] == "tools.google_workspace.gmail.trusted.contracts.no_emails"
    )


def test_identity_binds_defaults_and_typed_path_aliases_without_executing() -> None:
    @contract
    def helper(path: str, *, count: int = 2) -> ContractSpec:
        raise AssertionError("Identity presentation must not call contract bodies")

    filename = inspect.getsourcefile(helper)
    assert filename
    path = Path(filename).resolve()
    sources = {str(path): sha256(path.read_bytes()).hexdigest()}
    expression = ast.parse("alias(p('data'))", mode="eval").body
    assert isinstance(expression, ast.Call)
    identity = identify_call(
        helper,
        expression,
        aliases={"p": "clauz3.DirectoryPath"},
        roots=[path.parent],
        sources=sources,
    )
    assert str(identity["call"]).endswith(
        ".helper(path=clauz3.DirectoryPath('data'), count=2)"
    )
    with pytest.raises(ValueError, match="differs from the verified layer"):
        identify_call(helper, expression, aliases={}, roots=[path.parent], sources={})
    with pytest.raises(ValueError, match="outside the selected trusted roots"):
        identify_call(helper, expression, aliases={}, roots=[], sources=sources)


def test_html_identity_and_source_are_escaped_and_description_falls_back() -> None:
    expression = "rules.at_most(1)"
    page = render_guarantees(
        {
            "guarantees": [expression],
            "contract_identities": {
                expression: {
                    "call": "email.at_most(count=1)",
                    "symbol": "tools.email.trusted.contracts.at_most",
                    "title": "<script>Title</script>",
                    "source_file": "/x/<script>.py",
                    "source_code": 'def at_most(count):\n    "<script>"',
                }
            },
            "contract_documentation": {expression: "Summary.\n\nFull scope."},
        }
    )
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert '<p class="contract-description">Summary.</p>' in page
    assert page.index("email.at_most(count=1)") < page.index("Summary.")
    assert page.index("tools.email.trusted.contracts.at_most") < page.index("<details")
    assert page.index("<details") < page.index("rules.at_most(1)")
    assert "Full scope." in page


def test_positional_only_and_variadic_parameters_keep_valid_binding() -> None:
    def helper(
        first: int,
        /,
        second: int = 2,
        *remaining: int,
        enabled: bool = True,
        **options: int,
    ) -> ContractSpec:
        raise AssertionError("Identity presentation must not call contract bodies")

    filename = inspect.getsourcefile(helper)
    assert filename
    path = Path(filename).resolve()
    expression = ast.parse("alias(1, 3, 4, 5, more=6)", mode="eval").body
    assert isinstance(expression, ast.Call)
    identity = identify_call(
        helper,
        expression,
        aliases={},
        roots=[path.parent],
        sources={str(path): sha256(path.read_bytes()).hexdigest()},
    )
    assert str(identity["call"]).endswith(
        ".helper(1, 3, *(4, 5), enabled=True, **{'more': 6})"
    )
