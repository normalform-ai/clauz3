"""Only operator-configured layers may initialize during policy evaluation."""

import hashlib
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from clauz3.approvals.authority import (
    ApprovalAuthorizationError,
    ApprovedLayer,
    authorize_layer,
)
from clauz3.approvals.policy import ApprovalPolicy, Rule, evaluate_policy
from clauz3.approvals.receipts import (
    SIGNING_KEY_ENV,
    consent_digest,
    generate_keypair,
    verify_receipt,
)
from clauz3.approvals.service import create_approval_app


@dataclass(frozen=True)
class LayerFixture:
    package: str
    root: Path
    marker: Path

    @property
    def approved(self) -> ApprovedLayer:
        return ApprovedLayer((self.root,), (self.root.parent,))

    def request(self, *, body: str = "    pass\n") -> dict[str, object]:
        program = (
            f"from {self.package}.effects import record\ndef main() -> None:\n" + body
        )
        digest = hashlib.sha256(program.encode()).hexdigest()
        return {
            "request_id": "authority-" + digest[:12],
            "program_sha256": digest,
            "program": program,
            "target": "main",
            "trusted_roots": [str(self.root)],
            "import_roots": [str(self.root.parent)],
            "guarantees": [],
            "coverage": [],
        }

    def policy(self, *, empty: bool) -> ApprovalPolicy:
        return ApprovalPolicy(
            imports=(("checks", self.package + ".contracts"),),
            rules=(
                Rule("allow", "auto_approved", () if empty else ("checks.silent()",)),
            ),
        )


def _layer(
    parent: Path, *, package: str | None = None, allowed_records: int = 0
) -> LayerFixture:
    package = package or "approval_authority_" + uuid4().hex
    root = parent / package
    root.mkdir(parents=True)
    marker = parent / "initialized.txt"
    (root / "__init__.py").write_text("")
    (root / "effects.py").write_text(
        "from pathlib import Path\n"
        "import clauz3\n"
        f"Path({str(marker)!r}).write_text('initialized')\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (root / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        f"    return effect('record').count() <= {allowed_records}\n"
    )
    return LayerFixture(package, root, marker)


def test_authorization_matches_canonical_paths_without_mutating_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "trusted").mkdir()
    monkeypatch.chdir(tmp_path)
    approved = ApprovedLayer((Path("trusted"),), (Path("."),))
    request: dict[str, object] = {
        "trusted_roots": [str(tmp_path / "trusted")],
        "import_roots": [str(tmp_path)],
    }
    before = dict(request)

    assert approved.trusted_roots == ((tmp_path / "trusted").resolve(),)
    assert approved.import_roots == (tmp_path.resolve(),)
    assert authorize_layer(request, authorized_layers=(approved,)) is approved
    assert request == before


@pytest.mark.parametrize("field", ["trusted_roots", "import_roots"])
@pytest.mark.parametrize("value", [None, "root", [1], [""]])
def test_malformed_root_fields_are_not_silently_discarded(
    tmp_path: Path, field: str, value: object
) -> None:
    approved = ApprovedLayer((tmp_path,), (tmp_path,))
    request: dict[str, object] = {
        "trusted_roots": [str(tmp_path)],
        "import_roots": [str(tmp_path)],
        field: value,
    }
    with pytest.raises(ApprovalAuthorizationError):
        authorize_layer(request, authorized_layers=(approved,))


@pytest.mark.parametrize("field", ["trusted_roots", "import_roots"])
def test_both_root_fields_are_required(tmp_path: Path, field: str) -> None:
    request: dict[str, object] = {field: [str(tmp_path)]}
    with pytest.raises(ApprovalAuthorizationError):
        authorize_layer(
            request, authorized_layers=(ApprovedLayer((tmp_path,), (tmp_path,)),)
        )


@pytest.mark.parametrize("field", ["trusted_roots", "import_roots"])
def test_reordering_authorized_roots_is_a_different_layer(
    tmp_path: Path, field: str
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    approved = ApprovedLayer((first, second), (first, second))
    request: dict[str, object] = {
        "trusted_roots": [str(first), str(second)],
        "import_roots": [str(first), str(second)],
        field: [str(second), str(first)],
    }
    with pytest.raises(ApprovalAuthorizationError):
        authorize_layer(request, authorized_layers=(approved,))


def test_empty_roots_require_explicit_operator_authority() -> None:
    request: dict[str, object] = {"trusted_roots": [], "import_roots": []}
    with pytest.raises(ApprovalAuthorizationError):
        authorize_layer(request, authorized_layers=())
    approved = ApprovedLayer((), ())
    assert authorize_layer(request, authorized_layers=(approved,)) is approved


@pytest.mark.parametrize("empty", [False, True], ids=["nonempty", "empty"])
def test_policy_has_no_loading_authority_by_default(
    tmp_path: Path, empty: bool
) -> None:
    layer = _layer(tmp_path)
    with pytest.raises(ApprovalAuthorizationError):
        evaluate_policy(layer.policy(empty=empty), layer.request())
    assert not layer.marker.exists()
    assert layer.package + ".effects" not in sys.modules


@pytest.mark.parametrize("empty", [False, True], ids=["nonempty", "empty"])
@pytest.mark.parametrize("change", ["trusted-root", "added-import", "reordered-import"])
def test_request_cannot_expand_or_reorder_service_authority(
    tmp_path: Path, empty: bool, change: str
) -> None:
    approved = _layer(tmp_path / "approved")
    outsider = _layer(tmp_path / "outsider")
    request = approved.request()
    authority = approved.approved
    if change == "trusted-root":
        request = outsider.request()
    elif change == "added-import":
        request["import_roots"] = [str(outsider.root.parent), str(approved.root.parent)]
    else:
        authority = ApprovedLayer(
            (approved.root,), (approved.root.parent, outsider.root.parent)
        )
        request["import_roots"] = [str(outsider.root.parent), str(approved.root.parent)]
    app = create_approval_app(
        policy=approved.policy(empty=empty),
        authorized_layers=(authority,),
        max_wait_seconds=0.01,
    )
    client = TestClient(app)

    response = client.post("/requests", json=request)

    assert response.status_code == 403, response.text
    assert client.get(f"/requests/{request['request_id']}").status_code == 404
    assert not approved.marker.exists()
    assert not outsider.marker.exists()


@pytest.mark.parametrize("empty", [False, True], ids=["nonempty", "empty"])
def test_authorized_service_initializes_only_its_selected_layer(
    tmp_path: Path, empty: bool
) -> None:
    layer = _layer(tmp_path / "selected")
    unused = _layer(tmp_path / "unused")
    request = layer.request()
    client = TestClient(
        create_approval_app(
            policy=layer.policy(empty=empty),
            authorized_layers=(layer.approved, unused.approved),
        )
    )

    response = client.post("/requests", json=request)

    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "auto_approved"
    assert layer.marker.read_text() == "initialized"
    assert not unused.marker.exists()
    assert sys.modules[layer.package + ".effects"].EVENTS == []
    record = client.get(f"/requests/{request['request_id']}").json()
    for key, value in request.items():
        assert record[key] == value


@pytest.mark.parametrize("empty", [False, True], ids=["nonempty", "empty"])
def test_authority_does_not_bypass_module_effect_validation(
    tmp_path: Path, empty: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    layer = _layer(tmp_path)
    request = layer.request()
    request["program"] = (
        f"from {layer.package}.effects import record\n"
        "record('outside the entry')\n"
        "def main() -> None:\n    pass\n"
    )

    decision = evaluate_policy(
        layer.policy(empty=empty), request, authorized_layers=(layer.approved,)
    )

    assert decision is None
    assert "module-level" in capsys.readouterr().err
    assert layer.marker.exists()
    assert sys.modules[layer.package + ".effects"].EVENTS == []


@pytest.mark.parametrize("empty", [False, True], ids=["nonempty", "empty"])
def test_cached_approval_cannot_skip_layer_authorization(
    tmp_path: Path, empty: bool
) -> None:
    approved = _layer(tmp_path / "approved")
    outsider = _layer(tmp_path / "outsider")
    request = approved.request()
    client = TestClient(
        create_approval_app(
            policy=approved.policy(empty=empty), authorized_layers=(approved.approved,)
        )
    )
    original = client.post("/requests", json=request)
    assert original.status_code == 200
    assert original.json()["decision"] == "auto_approved"
    replaced = {**outsider.request(), "request_id": request["request_id"]}

    response = client.post("/requests", json=replaced)

    assert response.status_code == 403, response.text
    assert not outsider.marker.exists()
    record = client.get(f"/requests/{request['request_id']}").json()
    assert record["trusted_roots"] == request["trusted_roots"]
    assert record["approval"] == original.json()


def test_authorized_autoapproval_preserves_signed_receipt_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer = _layer(tmp_path)
    private, public = generate_keypair()
    monkeypatch.setenv(SIGNING_KEY_ENV, private)
    request = layer.request()
    client = TestClient(
        create_approval_app(
            policy=layer.policy(empty=False), authorized_layers=(layer.approved,)
        )
    )

    response = client.post("/requests", json=request)

    assert response.status_code == 200, response.text
    approval = response.json()
    assert approval["decision"] == "auto_approved"
    digest = str(request["program_sha256"])
    nonce = verify_receipt(
        verify_key_b64=public,
        receipt=approval["receipt"],
        request_id=str(request["request_id"]),
        program_sha256=digest,
        consent_sha256=consent_digest(
            program_sha256=digest, guarantees=[], coverage=[]
        ),
        now=time.time(),
    )
    assert nonce


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Authorization": "Bearer wrong"}, 401),
        (
            {"Authorization": "Bearer operator", "Origin": "http://foreign.example"},
            403,
        ),
    ],
    ids=["authentication", "origin"],
)
def test_authentication_and_origin_checks_precede_authorized_loading(
    tmp_path: Path, headers: dict[str, str], status: int
) -> None:
    layer = _layer(tmp_path)
    request = layer.request()
    client = TestClient(
        create_approval_app(
            policy=layer.policy(empty=False),
            authorized_layers=(layer.approved,),
            auth_token="operator",
            allowed_hosts=["testserver"],
        )
    )

    response = client.post("/requests", json=request, headers=headers)

    assert response.status_code == status, response.text
    assert not layer.marker.exists()
    assert client.get(f"/requests/{request['request_id']}").status_code == 404


def test_mutating_callers_registry_cannot_add_service_authority(tmp_path: Path) -> None:
    approved = _layer(tmp_path / "approved")
    outsider = _layer(tmp_path / "outsider")
    authorized_layers = [approved.approved]
    client = TestClient(
        create_approval_app(
            policy=approved.policy(empty=True), authorized_layers=authorized_layers
        )
    )
    authorized_layers.append(outsider.approved)

    response = client.post("/requests", json=outsider.request())

    assert response.status_code == 403, response.text
    assert not approved.marker.exists()
    assert not outsider.marker.exists()


@pytest.mark.parametrize("second_decision", ["auto_rejected", "ask"])
@pytest.mark.parametrize("copy_server_fields", [False, True], ids=["clean", "copied"])
def test_cached_approval_belongs_to_the_selected_authorized_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    second_decision: str,
    copy_server_fields: bool,
) -> None:
    package = "approval_authority_" + uuid4().hex
    first = _layer(tmp_path / "a", package=package, allowed_records=1)
    second = _layer(tmp_path / "b", package=package, allowed_records=0)
    layers = (first.approved, second.approved)
    allow = Rule("allow", "auto_approved", ("checks.silent()",))
    rules = (
        (Rule("deny", "auto_rejected", ("checks.silent()",)), allow)
        if second_decision == "auto_rejected"
        else (allow,)
    )
    policy = ApprovalPolicy(imports=(("checks", package + ".contracts"),), rules=rules)
    first_request = first.request(body="    record('one')\n")
    second_request = second.request(body="    record('one')\n")
    assert first_request["program"] == second_request["program"]
    assert first_request["request_id"] == second_request["request_id"]
    private, _public = generate_keypair()
    monkeypatch.setenv(SIGNING_KEY_ENV, private)
    client = TestClient(
        create_approval_app(
            policy=policy, authorized_layers=layers, max_wait_seconds=0.01
        )
    )
    detail_url = f"/requests/{first_request['request_id']}"

    with patch(
        "clauz3.approvals.service.evaluate_policy", wraps=evaluate_policy
    ) as evaluations:
        initial = client.post("/requests", json=first_request)
        assert initial.status_code == 200, initial.text
        initial_approval = initial.json()
        assert initial_approval["decision"] == "auto_approved"
        initial_record = client.get(detail_url).json()
        assert evaluations.call_count == 1

        # An unchanged request in the same selected layer still reuses its receipt.
        repeated = client.post("/requests", json=first_request)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json() == initial_approval
        assert evaluations.call_count == 1

        if copy_server_fields:
            second_request["approval"] = initial_record["approval"]
            second_request["auto_decision"] = initial_record["auto_decision"]
        changed = client.post("/requests", json=second_request)
        if second_decision == "ask":
            assert changed.status_code == 503, changed.text
        else:
            assert changed.status_code == 200, changed.text
            assert changed.json()["decision"] == "auto_rejected", changed.text
            assert "receipt" not in changed.json()
        assert evaluations.call_count == 2
        changed_record = client.get(detail_url).json()
        assert changed_record["trusted_roots"] == second_request["trusted_roots"]
        if second_decision == "ask":
            assert changed_record["status"] == "pending"
            assert "approval" not in changed_record
            assert "auto_decision" not in changed_record
        else:
            assert changed_record["status"] == "denied"
            assert changed_record["auto_decision"]["rule"] == "deny"
        assert second.marker.exists()

        # Returning to A re-evaluates A instead of reusing B's denial/pending state.
        returned = client.post("/requests", json=first_request)
        assert returned.status_code == 200, returned.text
        assert returned.json()["decision"] == "auto_approved"
        assert returned.json()["receipt"] != initial_approval["receipt"]
        assert evaluations.call_count == 3
        assert client.post("/requests", json=first_request).json() == returned.json()
        assert evaluations.call_count == 3


def test_pending_request_cannot_receive_another_authorized_layers_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = "approval_authority_" + uuid4().hex
    pending_layer = _layer(tmp_path / "pending", package=package)
    approved_layer = _layer(tmp_path / "approved", package=package, allowed_records=1)
    pending_request = pending_layer.request(body="    record('one')\n")
    approved_request = approved_layer.request(body="    record('one')\n")
    assert pending_request["request_id"] == approved_request["request_id"]
    private, _public = generate_keypair()
    monkeypatch.setenv(SIGNING_KEY_ENV, private)
    client = TestClient(
        create_approval_app(
            policy=pending_layer.policy(empty=False),
            authorized_layers=(pending_layer.approved, approved_layer.approved),
            max_wait_seconds=10,
        )
    )
    detail_url = f"/requests/{pending_request['request_id']}"

    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(client.post, "/requests", json=pending_request)
        for _ in range(200):
            record = client.get(detail_url)
            if record.status_code == 200 and record.json()["status"] == "pending":
                break
            time.sleep(0.01)
        else:
            pytest.fail("first request did not reach its pending state")
        assert not waiting.done()
        approved = client.post("/requests", json=approved_request)
        assert approved.status_code == 200, approved.text
        assert approved.json()["decision"] == "auto_approved"
        assert "receipt" in approved.json()
        superseded = waiting.result(timeout=5)

    assert superseded.status_code == 409, superseded.text
    assert superseded.json()["detail"] == (
        "request superseded by a different authorized layer"
    )
    assert "receipt" not in superseded.json()
    record = client.get(detail_url).json()
    assert record["trusted_roots"] == approved_request["trusted_roots"]
    assert record["approval"] == approved.json()


def test_policy_evaluation_uses_the_authorized_snapshot_of_request_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = "approval_authority_" + uuid4().hex
    first = _layer(tmp_path / "a", package=package, allowed_records=1)
    second = _layer(tmp_path / "b", package=package)
    alias = tmp_path / "selected"
    alias.symlink_to(first.root.parent, target_is_directory=True)
    request = first.request(body="    record('one')\n")
    request["trusted_roots"] = [str(alias / package)]
    request["import_roots"] = [str(alias)]
    selected: list[ApprovedLayer] = []

    def authorize_and_retarget(
        request: dict[str, object], *, authorized_layers: Sequence[ApprovedLayer]
    ) -> ApprovedLayer:
        layer = authorize_layer(request, authorized_layers=authorized_layers)
        selected.append(layer)
        alias.unlink()
        alias.symlink_to(second.root.parent, target_is_directory=True)
        return layer

    monkeypatch.setattr(
        "clauz3.approvals.service.authorize_layer", authorize_and_retarget
    )
    client = TestClient(
        create_approval_app(
            policy=first.policy(empty=False),
            authorized_layers=(first.approved, second.approved),
            max_wait_seconds=0.01,
        )
    )

    response = client.post("/requests", json=request)

    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "auto_approved"
    assert selected == [first.approved]
    assert first.marker.exists()
    assert not second.marker.exists()
    assert alias.resolve() == second.root.parent
    record = client.get(f"/requests/{request['request_id']}").json()
    for key, value in request.items():
        assert record[key] == value
