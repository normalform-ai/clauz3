from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from clauz3._vendor.deal_solver import Conclusion, Proof
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._funcs import FUNCTIONS
from clauz3.prover import AgentTheorem, ProofResult, ProverConfigError, prove_text

ROOT = Path(__file__).resolve().parents[1]
FILESYSTEM_ROOT = ROOT / "src/clauz3/stdlib/filesystem"
TRUSTED_ROOT = FILESYSTEM_ROOT / "tools/filesystem/trusted"


def _prove(source: str) -> ProofResult:
    results = prove_text(
        source,
        import_paths=[FILESYSTEM_ROOT],
        trusted_roots=[TRUSTED_ROOT],
    )
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize(
    ("contract_import", "contract_call"),
    [
        (
            "from tools.filesystem.trusted.contracts import read_only",
            "read_only()",
        ),
        (
            "from tools.filesystem.trusted.contracts import read_only as ro",
            "ro()",
        ),
    ],
)
def test_bare_contract_import_rejects_write(
    contract_import: str,
    contract_call: str,
) -> None:
    result = _prove(f"""\
import clauz3
{contract_import}
from tools.filesystem.trusted.effects import write_file

@clauz3.guarantee({contract_call})
def main() -> None:
    write_file("/etc/passwd", "x")
""")

    assert result.proof.conclusion == Conclusion.FAIL


def test_bare_contract_import_accepts_compliant_program() -> None:
    result = _prove("""\
import clauz3
from tools.filesystem.trusted.contracts import read_only
from tools.filesystem.trusted.effects import read_file

@clauz3.guarantee(read_only())
def main() -> None:
    read_file("/repo/a.py")
""")

    assert result.proof.conclusion == Conclusion.OK


def test_bare_contract_import_passes_arguments_to_registered_solver() -> None:
    result = _prove("""\
import clauz3
from tools.filesystem.trusted.contracts import only_write_under
from tools.filesystem.trusted.effects import write_file

@clauz3.guarantee(only_write_under("/sandbox"))
def main() -> None:
    write_file("/etc/passwd", "x")
""")

    assert result.proof.conclusion == Conclusion.FAIL


def test_bare_contract_import_rejects_guarantee_false_over_no_facts() -> None:
    # Counting an empty effect set is zero: the registered helper must still
    # refute an impossible bound, even with no effect call to expose a bad model.
    result = _prove("""\
import clauz3
from tools.filesystem.trusted.contracts import writes_at_most

@clauz3.guarantee(writes_at_most(-1))
def main() -> None:
    pass
""")

    assert result.proof.conclusion is Conclusion.FAIL
    assert result.proof.description == "guarantee"
    assert result.proof.error is None


@pytest.fixture
def pure_helpers_layer(tmp_path: Path) -> Iterator[Path]:
    """A trusted root exporting a pure helper that is not a ``@contract``."""
    trusted_root = tmp_path / "pure_helpers_layer/trusted"
    trusted_root.mkdir(parents=True)
    # The ancestor outside the trusted root is an inert namespace package.
    (trusted_root / "__init__.py").write_text("")
    (trusted_root / "helpers.py").write_text("""\
def is_positive(value: int) -> bool:
    return value > 0
""")
    try:
        yield trusted_root
    finally:
        # Namespace parents have no source file for the shared cache fixture
        # to identify as temporary; do not retain this test's search path.
        sys.modules.pop("pure_helpers_layer", None)


def test_bare_imported_pure_helper_still_inlines_in_the_body(
    tmp_path: Path,
    pure_helpers_layer: Path,
) -> None:
    results = prove_text(
        """\
from pure_helpers_layer.trusted.helpers import is_positive

def main() -> None:
    assert is_positive(1)
""",
        import_paths=[tmp_path],
        trusted_roots=[pure_helpers_layer],
    )

    assert len(results) == 1
    assert results[0].proof.conclusion == Conclusion.OK


def test_bare_imported_pure_helper_is_not_a_guarantee(
    tmp_path: Path,
    pure_helpers_layer: Path,
) -> None:
    """Trusted is not enough: a guarantee must name a registered ``@contract``.

    ``is_positive(1)`` says nothing about the program's effects, so it is not
    a claim a human can consent to -- and the same shape with an untrusted
    definition was one of the vacuous-guarantee holes (issue #130).
    """

    with pytest.raises(ProverConfigError, match="does not call a registered"):
        prove_text(
            """\
import clauz3
from pure_helpers_layer.trusted.helpers import is_positive

@clauz3.guarantee(is_positive(1))
def main() -> None:
    pass
""",
            import_paths=[tmp_path],
            trusted_roots=[pure_helpers_layer],
        )


@pytest.mark.parametrize("during_proof", [False, True], ids=["ambient", "active"])
def test_bare_contract_registry_deletion_cannot_prove_forbidden_write(
    monkeypatch: MonkeyPatch,
    during_proof: bool,
) -> None:
    monkeypatch.syspath_prepend(str(FILESYSTEM_ROOT))
    importlib.import_module("tools.filesystem.trusted.contracts")
    registry_key = "tools.filesystem.trusted.contracts.read_only"
    if during_proof:
        original_prove = AgentTheorem.prove

        def prove_without_model(theorem: AgentTheorem) -> Proof:
            # Remove the selected model after registration replay. A genuine
            # miss at solver dispatch must still refuse instead of inlining.
            monkeypatch.delitem(FUNCTIONS, registry_key)
            return original_prove(theorem)

        monkeypatch.setattr(AgentTheorem, "prove", prove_without_model)
    else:
        # Ambient registry edits cannot suppress a verified layer's record.
        monkeypatch.delitem(FUNCTIONS, registry_key)

    result = _prove("""\
import clauz3
from tools.filesystem.trusted.contracts import read_only
from tools.filesystem.trusted.effects import write_file

@clauz3.guarantee(read_only())
def main() -> None:
    write_file("/etc/passwd", "x")
""")

    if not during_proof:
        assert result.proof.conclusion == Conclusion.FAIL
        assert result.proof.error is None
        return
    assert result.proof.conclusion == Conclusion.PARTIAL
    assert isinstance(result.proof.error, UnsupportedError)
    assert str(result.proof.error) == (
        "no definition for tools.filesystem.trusted.contracts.read_only"
    )


@pytest.mark.parametrize(
    "declaration,decorator",
    [
        ("from clauz3.spec import contract", "contract"),
        ("from clauz3.spec import contract as c", "c"),
        ("import clauz3", "clauz3.contract"),
    ],
)
def test_configured_contract_registry_miss_refuses_inlining(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    declaration: str,
    decorator: str,
) -> None:
    trusted_root = tmp_path / "tools/described/trusted"
    trusted_root.mkdir(parents=True)
    (trusted_root / "contracts.py").write_text(
        f"{declaration}\n"
        "from clauz3.spec import ContractSpec, no_guarantees\n"
        f'@{decorator}(description="No restrictions are imposed.")\n'
        "def unrestricted() -> ContractSpec:\n    return no_guarantees()\n",
        encoding="utf-8",
    )
    source = """\
import clauz3
from tools.described.trusted.contracts import unrestricted

@clauz3.guarantee(unrestricted())
def main() -> None:
    assert 1 == 1
"""
    # Establish that the temporary layer and configured decorator are valid.
    control = prove_text(source, trusted_roots=[trusted_root], import_paths=[tmp_path])
    assert len(control) == 1
    assert control[0].proof.conclusion is Conclusion.OK

    registry_key = "tools.described.trusted.contracts.unrestricted"
    original_prove = AgentTheorem.prove

    def prove_without_model(theorem: AgentTheorem) -> Proof:
        # Delete after selected-layer replay to exercise the dispatch guard.
        monkeypatch.delitem(FUNCTIONS, registry_key)
        return original_prove(theorem)

    monkeypatch.setattr(AgentTheorem, "prove", prove_without_model)
    results = prove_text(source, trusted_roots=[trusted_root], import_paths=[tmp_path])
    assert len(results) == 1
    result = results[0]
    assert result.proof.conclusion is Conclusion.PARTIAL
    assert isinstance(result.proof.error, UnsupportedError)
    assert str(result.proof.error) == f"no definition for {registry_key}"
