"""Org-namespaced ``tools/<org>/<domain>/trusted`` layers (agent-deal#41).

``clauz3 install`` copies nested domains, so every command that discovers
trusted roots by default must find them by the same rule, and must not let a
directory that cannot be imported abort a run.
"""

import doctest
import io
import json
import shutil
import sys
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from clauz3.approvals.client import APPROVAL_SERVICE_ENV, MockApprovalServer
from clauz3.approvals.receipts import SIGNING_KEY_ENV, VERIFY_KEY_ENV, generate_keypair
from clauz3.cli import main
from clauz3.libraries import layout
from clauz3.libraries.discovery import installed_domains
from clauz3.libraries.install import InstallError, install_layer
from clauz3.libraries.layout import (
    SkippedLayerWarning,
    expand_import_roots,
    trusted_dirs,
)
from clauz3.runner import discover_trusted_roots

ROOT = Path(__file__).resolve().parents[1]
EMAIL_LAYER = ROOT / "examples/email/tools/email"

NESTED_SOURCE = """
import clauz3
from tools.acme.email.trusted import contracts as emails
from tools.acme.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    send_email("bob@example.com", "Report is ready")
"""


def write_effect(trusted: Path, name: str) -> Path:
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text(
        f"import clauz3\n\n\n@clauz3.trusted\ndef {name}(plate: str) -> None:\n"
        f'    """Effect {name}."""\n'
    )
    return trusted


def nest_email_layer(tools: Path) -> Path:
    """Copy the email example to ``tools/acme/email`` with its imports renamed."""
    domain = tools / "acme/email"
    shutil.copytree(EMAIL_LAYER, domain, ignore=shutil.ignore_patterns("__pycache__"))
    for path in domain.rglob("*.py"):
        path.write_text(
            path.read_text().replace("tools.email.trusted", "tools.acme.email.trusted")
        )
    return domain / "trusted"


@pytest.fixture
def approval_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    signing, verify = generate_keypair()
    monkeypatch.setenv(SIGNING_KEY_ENV, signing)
    monkeypatch.setenv(VERIFY_KEY_ENV, verify)
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "cache"))


def test_layout_doctests() -> None:
    assert doctest.testmod(layout).failed == 0


def test_flat_and_nested_domains_are_discovered(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    flat = write_effect(tools / "lims/trusted", "pipette")
    nested = write_effect(tools / "acme/lims/trusted", "acme_pipette")

    assert trusted_dirs(tools) == [nested, flat]
    assert discover_trusted_roots(import_roots=[tmp_path]) == [nested, flat]
    assert installed_domains(tmp_path) == {"lims", "acme/lims"}


@pytest.mark.parametrize(
    ("domain", "reason"),
    [
        ("acme-labs/lims", "'acme-labs' is not a Python identifier"),
        ("acme/lims-v2", "'lims-v2' is not a Python identifier"),
        ("class", "'class' is a Python keyword"),
        ("a/b/c", "domains nest at most 2 levels"),
    ],
)
def test_unusable_domain_is_skipped_with_a_warning(
    tmp_path: Path, domain: str, reason: str
) -> None:
    tools = tmp_path / "tools"
    good = write_effect(tools / "email/trusted", "send")
    write_effect(tools / domain / "trusted", "other")

    with pytest.warns(SkippedLayerWarning, match=reason):
        roots = discover_trusted_roots(import_roots=[tmp_path])

    assert roots == [good]


def test_trusted_dir_inside_an_accepted_root_is_part_of_it(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    outer = write_effect(tools / "acme/trusted", "outer")
    write_effect(outer / "trusted", "inner")
    write_effect(outer / "sub/trusted", "deeper")

    assert trusted_dirs(tools) == [outer]


def test_symlinked_domain_is_excluded(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    real = write_effect(tools / "email/trusted", "send")
    outside = write_effect(tmp_path / "outside/lims/trusted", "pipette")
    (tools / "acme").mkdir()
    (tools / "acme/lims").symlink_to(outside.parent, target_is_directory=True)
    (tools / "linked").mkdir()
    (tools / "linked/trusted").symlink_to(outside, target_is_directory=True)

    assert trusted_dirs(tools) == [real]


def test_bare_tools_trusted_is_not_a_domain(tmp_path: Path) -> None:
    write_effect(tmp_path / "tools/trusted", "send")

    with pytest.warns(SkippedLayerWarning, match="needs a domain directory"):
        assert trusted_dirs(tmp_path / "tools") == []


def test_install_uses_the_same_rule(tmp_path: Path) -> None:
    source = tmp_path / "source/tools"
    write_effect(source / "acme/lims/trusted", "pipette")
    write_effect(source / "acme-labs/madsci/trusted", "move")
    write_effect(source / "a/b/c/trusted", "deep")

    with pytest.warns(SkippedLayerWarning):
        result = install_layer(source, into=tmp_path / "dest")

    assert result.domains == ["acme/lims"]
    assert not (tmp_path / "dest/.clauz3/tools/acme-labs").exists()


def test_install_of_only_unimportable_domains_fails(tmp_path: Path) -> None:
    source = tmp_path / "source/tools"
    write_effect(source / "acme-labs/lims/trusted", "pipette")

    with (
        pytest.warns(SkippedLayerWarning),
        pytest.raises(InstallError, match="no trusted domains"),
    ):
        install_layer(source, into=tmp_path / "dest")


def test_tools_listing_distinguishes_flat_and_nested(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """The same effect name in ``lims`` and ``acme/lims`` lists as two paths.

    Each shown path is the import path below ``tools``, so an agent cannot
    mistake ``tools.acme.lims`` for ``tools.lims``.
    """
    write_effect(tmp_path / "tools/lims/trusted", "pipette")
    write_effect(tmp_path / ".clauz3/tools/acme/lims/trusted", "pipette")

    assert (
        main(["tools", "--import-roots", str(tmp_path / ".clauz3"), str(tmp_path)]) == 0
    )

    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "effect acme/lims/trusted/effects.py:pipette(plate)",
        "effect lims/trusted/effects.py:pipette(plate)",
    ]


def test_run_discovers_an_installed_nested_domain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: CaptureFixture[str],
    approval_keys: None,
) -> None:
    nest_email_layer(tmp_path / ".clauz3/tools")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(NESTED_SOURCE))

    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        assert main(["run"]) == 0

    assert len(server.requests) == 1
    assert server.requests[0]["trusted_roots"] == [
        str(tmp_path / ".clauz3/tools/acme/email/trusted")
    ]
    assert "main: proved!" in capsys.readouterr().out


def test_unimportable_sibling_does_not_abort_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: CaptureFixture[str],
    approval_keys: None,
) -> None:
    nest_email_layer(tmp_path / ".clauz3/tools")
    write_effect(tmp_path / ".clauz3/tools/acme-labs/lims/trusted", "pipette")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(NESTED_SOURCE))

    with (
        MockApprovalServer(config={"decision": "approved_once"}) as server,
        pytest.warns(SkippedLayerWarning, match="acme-labs"),
    ):
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        assert main(["run"]) == 0

    assert len(server.requests) == 1
    assert "main: proved!" in capsys.readouterr().out


def test_policy_check_discovers_a_nested_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    nest_email_layer(tmp_path / ".clauz3/tools")
    program = tmp_path / "plan.py"
    program.write_text(NESTED_SOURCE)
    policy = tmp_path / "approval-policy.json"
    policy.write_text(
        json.dumps(
            {
                "version": 1,
                "imports": {"pol": "tools.acme.email.trusted.contracts"},
                "rules": [
                    {
                        "name": "bob-only",
                        "decision": "auto_approved",
                        "when_proven": ["pol.only(['bob@example.com'])"],
                    }
                ],
            }
        )
    )
    monkeypatch.chdir(tmp_path)

    assert main(["policy-check", str(program), "--policy", str(policy)]) == 0

    assert capsys.readouterr().out.startswith("auto_approved: bob-only")


def test_expand_import_roots_finds_nested_install(tmp_path: Path) -> None:
    trusted = nest_email_layer(tmp_path / ".clauz3/tools")

    assert discover_trusted_roots(import_roots=expand_import_roots([tmp_path])) == [
        trusted
    ]
