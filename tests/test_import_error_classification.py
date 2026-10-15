"""Import configuration failures retain the public API's exception family."""

from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3.imports_policy import build_policy
from clauz3.interp import TrustedBoundaryError, execute
from clauz3.prover import ProverConfigError, prove_text
from clauz3.runner import RunConfigError, _execute_source, run_source


@pytest.mark.parametrize("runner", [False, True], ids=["interpreter", "runner"])
def test_missing_allowed_import_is_a_configuration_error(runner: bool) -> None:
    name = f"missing_import_{uuid4().hex}"
    source = f"import {name}\ndef main() -> None:\n    assert True\n"
    policy = build_policy(trusted_roots=[], import_roots=[], extra_allowed=[name])

    if runner:
        with pytest.raises(RunConfigError, match=f"cannot resolve import '{name}'"):
            _execute_source(
                source,
                source_name="plan.py",
                target="main",
                import_roots=[],
                request_id="classification-test",
                receipt="unused",
                policy=policy,
            )
    else:
        with pytest.raises(
            TrustedBoundaryError, match=f"cannot resolve import '{name}'"
        ):
            execute(source, source_name="plan.py", policy=policy)


def test_raw_prover_classifies_package_marker_parent_as_configuration_error(
    tmp_path: Path,
) -> None:
    package = tmp_path / f"marker_parent_{uuid4().hex}"
    trusted = package / "trusted"
    trusted.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (trusted / "effects.py").write_text("")

    with pytest.raises(ProverConfigError, match="parent module.*outside"):
        prove_text(
            "def main() -> None:\n    assert True\n",
            trusted_roots=[trusted],
            import_paths=[tmp_path],
        )


def test_raw_prover_classifies_missing_trusted_import_as_configuration_error(
    tmp_path: Path,
) -> None:
    package = tmp_path / f"missing_trusted_dependency_{uuid4().hex}"
    package.mkdir()
    (package / "effects.py").write_text(f"import absent_{uuid4().hex}\n")

    with pytest.raises(ProverConfigError, match="No module named 'absent_"):
        prove_text(
            "def main() -> None:\n    assert True\n",
            trusted_roots=[package],
            import_paths=[tmp_path],
        )


def test_trusted_layout_changing_after_preflight_is_a_run_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clauz3 import runner

    parent = tmp_path / f"changed_parent_{uuid4().hex}"
    trusted = parent / "trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text("")
    original_preflight = runner._preflight_structure

    def preflight_then_change_layout(**kwargs: Any) -> None:
        original_preflight(**kwargs)
        (parent / "__init__.py").write_text("")

    approval = Mock(side_effect=AssertionError("approval must not be reached"))
    monkeypatch.setattr(runner, "_preflight_structure", preflight_then_change_layout)
    monkeypatch.setattr(runner, "submit_approval_request", approval)
    with pytest.raises(RunConfigError, match="parent module.*outside"):
        run_source(
            "def main() -> None:\n    assert True\n",
            source_name="plan.py",
            trusted_roots=[trusted],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )
    approval.assert_not_called()
