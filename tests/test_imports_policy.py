from pathlib import Path

import pytest

from clauz3.imports_policy import (
    DEFAULT_STDLIB_ALLOWED,
    DisallowedImportError,
    ImportPolicy,
    build_policy,
    validate_imports,
)
from clauz3.prover import ProverConfigError, prove_path, prove_text

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
EMAIL_TRUSTED = EMAIL_ROOT / "tools/email/trusted"


def _policy() -> ImportPolicy:
    return build_policy(
        trusted_roots=[EMAIL_TRUSTED],
        import_roots=[EMAIL_ROOT],
    )


def test_default_policy_allows_clauz3_and_safe_stdlib() -> None:
    policy = build_policy()
    assert policy.is_allowed("clauz3")
    assert policy.is_allowed("clauz3.guarantee")
    for module in DEFAULT_STDLIB_ALLOWED:
        assert policy.is_allowed(module), module


def test_default_policy_rejects_world_reading_modules() -> None:
    policy = build_policy()
    for module in (
        "os",
        "subprocess",
        "socket",
        "pickle",
        "marshal",
        "urllib",
        "requests",
        "tempfile",
        "webbrowser",
        "time",
        "random",
        "datetime",
        "pathlib",
        "multiprocessing",
    ):
        assert not policy.is_allowed(module), module


def test_trusted_root_prefix_is_derived() -> None:
    policy = _policy()
    assert policy.is_allowed("tools.email.trusted")
    assert policy.is_allowed("tools.email.trusted.effects")
    assert policy.is_allowed("tools.email.trusted.contracts")
    # Sibling untrusted siblings are NOT allowed — derivation is specific.
    assert not policy.is_allowed("tools.email")
    assert not policy.is_allowed("tools")
    assert not policy.is_allowed("tools.email.untrusted")


def test_extra_allowed_single_name_becomes_root() -> None:
    policy = build_policy(extra_allowed=["pickle"])
    assert policy.is_allowed("pickle")
    assert policy.is_allowed("pickle.something")


def test_extra_allowed_dotted_name_becomes_prefix_only() -> None:
    policy = build_policy(extra_allowed=["tools.legacy.helpers"])
    assert policy.is_allowed("tools.legacy.helpers")
    assert policy.is_allowed("tools.legacy.helpers.util")
    assert not policy.is_allowed("tools.legacy")
    assert not policy.is_allowed("tools")


def test_validate_imports_rejects_disallowed_module() -> None:
    with pytest.raises(DisallowedImportError, match="'os'"):
        validate_imports(
            source="import os\n",
            source_name="<test>",
            policy=build_policy(),
        )


def test_validate_imports_rejects_relative_import() -> None:
    with pytest.raises(DisallowedImportError, match="relative imports"):
        validate_imports(
            source="from . import sibling\n",
            source_name="<test>",
            policy=build_policy(),
        )


def test_prove_text_rejects_disallowed_import() -> None:
    """Closes the gap: prove path used to ignore imports entirely."""
    source = "import os\nimport clauz3\n\ndef main() -> None:\n    pass\n"
    with pytest.raises(ProverConfigError, match="'os'"):
        prove_text(source)


def test_prove_text_accepts_extra_allowed_import() -> None:
    source = "import os\nimport clauz3\n\ndef main() -> None:\n    pass\n"
    results = prove_text(source, extra_allowed_imports=["os"])
    assert all(r.ok for r in results)


def test_prove_path_rejects_disallowed_import(tmp_path: Path) -> None:
    entry = tmp_path / "plan.py"
    entry.write_text(
        "import socket\n"
        "import clauz3\n"
        "from tools.email.trusted import contracts as emails\n"
        "from tools.email.trusted.effects import send_email\n"
        "\n"
        "@clauz3.guarantee(emails.only(['bob@example.com']))\n"
        "@clauz3.guarantee(emails.unique_recipients())\n"
        "def main() -> None:\n"
        "    send_email('bob@example.com', 'hi')\n"
    )
    with pytest.raises(ProverConfigError, match="'socket'"):
        prove_path(entry, trusted_roots=[EMAIL_TRUSTED], import_roots=[EMAIL_ROOT])


def test_operator_is_not_default_allowed() -> None:
    """Issue #68: ``operator.attrgetter`` takes a dunder name as a *string*.

    Under the old ``exec`` runtime that slipped past an AST denylist matching
    attribute syntax, which is why ``operator`` was removed from the defaults.
    The interpreter has no general attribute access at all, so the bypass has
    nothing to reach -- but the allowlist is shared with ``prove`` and the
    module is still unmodelled, so it stays out.
    """
    from clauz3.imports_policy import DEFAULT_STDLIB_ALLOWED

    assert "operator" not in DEFAULT_STDLIB_ALLOWED
