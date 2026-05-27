"""Regression test for keyword-arg handling in trusted-call facts.

Background: the ``clauz3-tools-assistant`` design (see
``docs/todos/clauz3-tools-assistant.md``) relies on a dry-run safety pattern
where every send-side trusted function carries a ``dry_run: bool = True``
keyword-only parameter, and a ``dry_run_only()`` contract is used to prove
that no real send fires.

For that design to be sound, three things must hold:

1. A trusted call passing ``dry_run=True`` explicitly must prove.
2. A trusted call passing ``dry_run=False`` explicitly must fail.
3. A trusted call relying on the default (not passing the kwarg at all)
   must NOT prove — i.e. the prover must fail-closed when the contract
   queries a field that isn't in the recorded fact, rather than silently
   approving.

This third behaviour is the load-bearing one. If a default kwarg silently
proved against ``e.dry_run == True``, an agent could write
``send_email("alice@example.com")`` and get approved without ever stating
its intent, defeating the safety pattern.

These tests pin all three behaviours so a future change to trusted-call
fact recording doesn't accidentally turn defaults into reflected fields
without a corresponding design update.
"""

from pathlib import Path

from clauz3.prover import prove_path

_EFFECTS_SRC = """\
import deal


@deal.pre(lambda to, dry_run: "@" in to, message="addr needs @")
@deal.has("trusted")
def send(to: str, *, dry_run: bool = True) -> None:
    pass
"""

_CONTRACTS_SRC = """\
from clauz3.spec import ContractSpec, contract, effect

Send = effect("send")


@contract
def dry_run_only() -> ContractSpec:
    return Send.all(lambda e: e.dry_run == True)
"""


def _setup_layer(tmp_path: Path) -> tuple[Path, Path]:
    """Write a minimal trusted layer with a dry_run kwarg into tmp_path."""
    trusted = tmp_path / "tools" / "sender" / "trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text(_EFFECTS_SRC)
    (trusted / "contracts.py").write_text(_CONTRACTS_SRC)
    return trusted, tmp_path


def _write_case(tmp_path: Path, name: str, body: str) -> Path:
    case = tmp_path / name
    case.write_text(
        "# ruff: noqa: F821\n"
        "import clauz3\n"
        "from tools.sender.trusted import contracts as snd\n"
        "from tools.sender.trusted.effects import send\n"
        "\n"
        "@clauz3.guarantee(snd.dry_run_only())\n"
        "def main() -> None:\n"
        f"{body}\n"
    )
    return case


def test_explicit_dry_run_true_proves(tmp_path: Path) -> None:
    """Explicit ``dry_run=True`` proves against ``dry_run_only()``."""
    trusted, root = _setup_layer(tmp_path)
    case = _write_case(
        tmp_path,
        "explicit_true.py",
        '    send("alice@example.com", dry_run=True)',
    )
    results = prove_path(case, trusted_roots=[trusted], import_roots=[root])
    assert len(results) == 1
    assert results[0].ok is True


def test_explicit_dry_run_false_fails(tmp_path: Path) -> None:
    """Explicit ``dry_run=False`` is correctly rejected."""
    trusted, root = _setup_layer(tmp_path)
    case = _write_case(
        tmp_path,
        "explicit_false.py",
        '    send("alice@example.com", dry_run=False)',
    )
    results = prove_path(case, trusted_roots=[trusted], import_roots=[root])
    assert len(results) == 1
    assert results[0].ok is False


def test_default_kwarg_fails_closed(tmp_path: Path) -> None:
    """Relying on the kwarg default does NOT prove (fail-closed).

    This is the load-bearing behaviour for the dry-run safety pattern: the
    default value must not be reflected into the recorded fact, so a
    contract that queries the field correctly rejects implicit calls. The
    agent has to *intentionally* pass ``dry_run=True`` to earn approval.
    """
    trusted, root = _setup_layer(tmp_path)
    case = _write_case(
        tmp_path,
        "default.py",
        '    send("alice@example.com")',
    )
    results = prove_path(case, trusted_roots=[trusted], import_roots=[root])
    assert len(results) == 1
    assert results[0].ok is False
