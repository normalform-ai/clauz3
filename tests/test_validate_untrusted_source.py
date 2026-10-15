"""Issue #75: trusted-only decorators must be rejected in untrusted entry files
even when reached through an import alias.

``validate_untrusted_source`` forbids ``@deal.has`` / ``@contract`` /
``@effect`` / ``@solver`` in untrusted source. The check must resolve import
aliases back to their canonical target, or an alias trivially defeats it.
"""

from pathlib import Path

import pytest

from clauz3.prover import ProverConfigError, validate_untrusted_source


def _check(source: str) -> None:
    validate_untrusted_source(source=source, path=Path("plan.py"))


# ── allowed forms (must NOT raise) ────────────────────────────────────────────


def test_guarantee_is_allowed() -> None:
    _check("import clauz3\n@clauz3.guarantee(None)\ndef main() -> None:\n    pass\n")


def test_plain_undecorated_is_allowed() -> None:
    _check("def main() -> None:\n    pass\n")


# ── direct forms (already rejected before the fix) ────────────────────────────


def test_direct_deal_has_rejected() -> None:
    with pytest.raises(ProverConfigError):
        _check('import deal\n@deal.has("trusted")\ndef f() -> None:\n    pass\n')


# ── aliased forms (the bypass — must now be rejected) ──────────────────────────


def test_from_import_alias_has_rejected() -> None:
    with pytest.raises(ProverConfigError):
        _check('from deal import has as h\n@h("trusted")\ndef f() -> None:\n    pass\n')


def test_module_alias_has_rejected() -> None:
    with pytest.raises(ProverConfigError):
        _check('import deal as d\n@d.has("trusted")\ndef f() -> None:\n    pass\n')


def test_from_import_alias_contract_rejected() -> None:
    with pytest.raises(ProverConfigError):
        _check(
            "from clauz3.spec import contract as c\n@c\ndef f() -> None:\n    pass\n"
        )


def test_from_import_alias_effect_rejected() -> None:
    with pytest.raises(ProverConfigError):
        _check(
            "from clauz3.fluent import effect as e\n"
            "@e(lambda: None)\n"
            "def f() -> None:\n"
            "    pass\n"
        )


def test_module_alias_spec_contract_rejected() -> None:
    with pytest.raises(ProverConfigError):
        _check("import clauz3.spec as s\n@s.contract\ndef f() -> None:\n    pass\n")


# ── clauz3 top-level re-export forms ──────────────────────────────────────────
# clauz3/__init__.py re-exports the trusted decorators: ``clauz3.contract`` IS
# ``clauz3.spec.contract`` and ``clauz3.effect`` IS the trusted effect. These
# must be rejected too — and ``from clauz3 import contract`` was rejected on
# main, so letting it through is a regression, not just a missed alias.


def test_clauz3_contract_attribute_rejected() -> None:
    with pytest.raises(ProverConfigError):
        _check("import clauz3\n@clauz3.contract\ndef f() -> None:\n    pass\n")


def test_clauz3_effect_attribute_rejected() -> None:
    with pytest.raises(ProverConfigError):
        _check('import clauz3\n@clauz3.effect("x")\ndef f() -> None:\n    pass\n')


def test_clauz3_module_alias_contract_rejected() -> None:
    with pytest.raises(ProverConfigError):
        _check("import clauz3 as cz\n@cz.contract\ndef f() -> None:\n    pass\n")


def test_from_clauz3_import_contract_rejected() -> None:
    with pytest.raises(ProverConfigError):
        _check("from clauz3 import contract\n@contract\ndef f() -> None:\n    pass\n")


def test_from_clauz3_import_effect_alias_rejected() -> None:
    with pytest.raises(ProverConfigError):
        _check("from clauz3 import effect as e\n@e('x')\ndef f() -> None:\n    pass\n")


def test_clauz3_solver_still_rejected() -> None:
    with pytest.raises(ProverConfigError):
        _check("import clauz3\n@clauz3.solver\ndef f() -> None:\n    pass\n")
