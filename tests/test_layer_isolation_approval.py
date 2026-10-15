"""Approval selection is repeatable, and an approval wait releases selection."""

import hashlib
import importlib
import sys
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread
from typing import cast
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.approvals.authority import ApprovedLayer
from clauz3.approvals.client import ApprovalResponse
from clauz3.approvals.policy import ApprovalPolicy, Rule, evaluate_policy
from clauz3.approvals.receipts import (
    VERIFY_KEY_ENV,
    consent_digest,
    generate_keypair,
    sign_receipt,
)
from clauz3.prover import prove_text
from clauz3.runner import run_source


def _layer(parent: Path, name: str, *, allowed: bool, label: str) -> Path:
    layer = parent / name
    layer.mkdir(parents=True)
    (layer / "__init__.py").write_text("")
    (parent / f"{name}_authority.py").write_text(f"LABEL = {label!r}\n")
    # B selects its sibling during initialization. A first imports its own
    # sibling inside the real effect, after the approval interleave.
    dependency = f"import {name}_authority\n" if label == "B" else ""
    (layer / "effects.py").write_text(
        "import clauz3\n" + dependency + "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        f"@clauz3.ensures(lambda result: result == {allowed!r})\n"
        "def allowed() -> bool:\n"
        f"    return {allowed!r}\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        f"    from {name}_authority import LABEL\n"
        "    EVENTS.append(LABEL + ':' + value)\n"
    )
    (layer / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect('record').empty()\n"
        "@contract\n"
        "def once() -> ContractSpec:\n"
        "    return effect('record').count() <= 1\n"
    )
    return layer


def _program(name: str) -> str:
    return (
        f"from {name}.effects import allowed, record\n"
        "def main() -> None:\n"
        "    if not allowed():\n"
        "        record('bad')\n"
    )


def _request(layer: Path) -> dict[str, object]:
    return {
        "program": _program(layer.name),
        "target": "main",
        "trusted_roots": [str(layer)],
        "import_roots": [str(layer.parent)],
    }


@pytest.mark.parametrize("kind", ["empty", "nonempty", "multiple"])
@pytest.mark.parametrize("concurrent", [False, True])
def test_repeated_approval_requests_select_same_named_layers_without_cleanup(
    tmp_path: Path, kind: str, concurrent: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This test checks serialized decisions, not lock admission timing. A worker
    # may wait behind several complete requests because RLock is not fair.
    # The bounded-refusal test separately pins the production timeout behavior.
    monkeypatch.setattr("clauz3.layer_state.LOCK_TIMEOUT", 60.0)
    name = f"approval_layers_{uuid4().hex}"
    layers = [
        _layer(tmp_path / "a", name, allowed=True, label="A"),
        _layer(tmp_path / "b", name, allowed=False, label="B"),
    ]
    rules: tuple[Rule, ...]
    expected: list[str | None]
    if kind == "empty":
        rules = (Rule("allow", "auto_approved", ()),)
        expected = ["auto_approved", "auto_approved"]
    elif kind == "nonempty":
        rules = (Rule("silent", "auto_approved", ("checks.silent()",)),)
        expected = ["auto_approved", None]
    else:
        rules = (
            Rule("deny-silent", "auto_rejected", ("checks.silent()",)),
            Rule("allow-once", "auto_approved", ("checks.once()",)),
        )
        expected = ["auto_approved", "auto_rejected"]
    policy = ApprovalPolicy(imports=(("checks", name + ".contracts"),), rules=rules)

    authorized_layers = tuple(
        ApprovedLayer((layer,), (layer.parent,)) for layer in layers
    )

    def decide(index: int) -> str | None:
        result = evaluate_policy(
            policy, _request(layers[index]), authorized_layers=authorized_layers
        )
        return None if result is None else result.decision

    # All requests belong to one test: the autouse cleanup runs only afterward.
    order = [0, 1, 0, 1, 0, 1]
    if concurrent:
        with ThreadPoolExecutor(max_workers=2) as pool:
            actual = list(pool.map(decide, order))
    else:
        actual = [decide(index) for index in order]
    assert actual == [expected[index] for index in order]
    assert importlib.import_module(name + ".effects").EVENTS == []


def test_approval_wait_releases_selection_and_resumes_exact_layer_for_lazy_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = f"approval_resume_{uuid4().hex}"
    layer_a = _layer(tmp_path / "a", name, allowed=True, label="A")
    layer_b = _layer(tmp_path / "b", name, allowed=False, label="B")
    source = (
        "import clauz3\n"
        f"from {name}.effects import record\n"
        f"from {name}.contracts import once\n"
        "@clauz3.guarantee(once())\n"
        "def main() -> None:\n"
        "    record('one')\n"
    )
    source_b = f"import clauz3\nfrom {name}.contracts import silent\n" + _program(
        name
    ).replace("def main", "@clauz3.guarantee(silent())\ndef main")
    signing, verify = generate_keypair()
    monkeypatch.setenv(VERIFY_KEY_ENV, verify)
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "receipts"))
    conclusions: list[Conclusion] = []
    failures: list[BaseException] = []
    approvals: list[str] = []
    worker: Thread | None = None

    def interleave() -> None:
        try:
            conclusions.extend(
                result.proof.conclusion
                for result in prove_text(
                    source_b, trusted_roots=[layer_b], import_paths=[layer_b.parent]
                )
            )
        except BaseException as exc:
            failures.append(exc)

    def approve(url: str, request: Mapping[str, object]) -> ApprovalResponse:
        nonlocal worker
        effects = importlib.import_module(name + ".effects")
        assert effects.EVENTS == []
        assert name + "_authority" not in sys.modules
        approvals.append(str(request["request_id"]))
        worker = Thread(target=interleave, daemon=True)
        worker.start()
        worker.join(timeout=15)
        assert not worker.is_alive(), "approval wait retained the layer selection lock"
        assert failures == []
        assert conclusions == [Conclusion.FAIL]
        assert sys.modules[name + ".effects"] is effects
        assert effects.EVENTS == []
        digest = hashlib.sha256(source.encode()).hexdigest()
        receipt = sign_receipt(
            signing_key_b64=signing,
            request_id=str(request["request_id"]),
            program_sha256=digest,
            decision="approved_once",
            expires_at=time.time() + 60,
            nonce=uuid4().hex,
            consent_sha256=consent_digest(
                program_sha256=digest,
                guarantees=cast(Sequence[str], request["guarantees"]),
                coverage=request.get("coverage"),
            ),
        )
        return ApprovalResponse(decision="approved_once", receipt=receipt)

    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    try:
        outcome = run_source(
            source,
            source_name="entry.py",
            trusted_roots=[layer_a],
            import_roots=[layer_a.parent],
            approval_service_url="http://approval.invalid",
        )
    finally:
        # A lock regression must not leave a worker touching later tests.
        if worker is not None:
            worker.join(timeout=15)
    assert len(approvals) == 1
    assert len(outcome.effects) == 1
    effects = importlib.import_module(name + ".effects")
    assert effects.EVENTS == ["A:one"]
