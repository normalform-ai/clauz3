"""Remembered decisions are verified authority, not cached execution receipts."""

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

from clauz3.approvals.authority import ApprovedLayer
from clauz3.approvals.descriptions import render_guarantees
from clauz3.approvals.policy import ApprovalPolicy
from clauz3.approvals.profiles.store import ProfileStore, objects
from clauz3.approvals.profiles.verification import _engine_identity, verify
from clauz3.approvals.receipts import SIGNING_KEY_ENV, generate_keypair
from clauz3.approvals.service import create_approval_app
from clauz3.prover import prove_text
from clauz3.runner import build_approval_request

ROOT = Path(__file__).resolve().parents[1]
OPERATOR = {"Authorization": "Bearer operator"}
AGENT = {"Authorization": "Bearer agent"}
SOURCE = """import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

@clauz3.guarantee(emails.only(["bob@example.com"]))
@clauz3.guarantee(emails.at_most(1))
def main() -> None:
    send_email("bob@example.com", "hello")
"""


@pytest.fixture
def layer(tmp_path: Path) -> ApprovedLayer:
    project = tmp_path / "project"
    shutil.copytree(ROOT / "examples/email/tools", project / "tools")
    return ApprovedLayer((project / "tools/email/trusted",), (project,))


def request(layer: ApprovedLayer, source: str = SOURCE) -> dict[str, object]:
    proofs = prove_text(
        source, trusted_roots=layer.trusted_roots, import_paths=layer.import_roots
    )
    return build_approval_request(
        source=source,
        source_name="<test>",
        target="main",
        proofs=proofs,
        trusted_roots=layer.trusted_roots,
        import_roots=layer.import_roots,
    )


def client(store: ProfileStore, layer: ApprovedLayer) -> TestClient:
    return TestClient(
        create_approval_app(
            profile=store,
            authorized_layers=(layer,),
            auth_token="agent",
            decision_token="operator",
            max_wait_seconds=0.01,
        )
    )


def decide(
    api: TestClient,
    payload: dict[str, object],
    *,
    decision: str = "approved_once",
    remember: bool = True,
) -> None:
    assert api.post("/requests", json=payload, headers=AGENT).status_code == 503
    response = api.post(
        f"/api/requests/{payload['request_id']}/decision",
        json={"decision": decision, "remember": remember},
        headers=OPERATOR,
    )
    assert response.status_code == 200, response.text


def test_remember_restart_changed_body_and_disable(
    tmp_path: Path, layer: ApprovedLayer, monkeypatch: pytest.MonkeyPatch
) -> None:
    signing, _ = generate_keypair()
    monkeypatch.setenv(SIGNING_KEY_ENV, signing)
    store = ProfileStore(
        tmp_path / "state/history.sqlite", project=layer.import_roots[0]
    )
    api = client(store, layer)
    first = request(layer)
    decide(api, first)
    restarted = client(ProfileStore(store.path, project=layer.import_roots[0]), layer)
    changed = request(layer, SOURCE.replace('"hello"', '"another message"'))
    response = restarted.post("/requests", json=changed, headers=AGENT)
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "auto_approved"
    again = restarted.post("/requests", json=changed, headers=AGENT)
    assert again.json()["receipt"] != response.json()["receipt"]
    snapshot = restarted.get("/api/profile", headers=OPERATOR).json()
    assert len(snapshot["history"]) == 3
    assert "receipt" not in json.dumps(snapshot)
    assert signing not in store.path.read_bytes().decode(errors="ignore")
    rule = snapshot["rules"][0]["id"]
    assert (
        restarted.post(
            "/api/profile/disable", json={"id": rule}, headers=OPERATOR
        ).status_code
        == 200
    )
    assert restarted.post("/requests", json=changed, headers=AGENT).status_code == 503


def test_historical_curation_auth_and_rejection(
    tmp_path: Path, layer: ApprovedLayer
) -> None:
    store = ProfileStore(tmp_path / "history.sqlite", project=layer.import_roots[0])
    api = client(store, layer)
    payload = request(layer)
    decide(api, payload, decision="rejected", remember=False)
    event = objects(store.snapshot()["history"])[0]["id"]
    assert api.get("/api/profile", headers=AGENT).status_code == 401
    assert api.get("/ui/profile").status_code == 401
    assert (
        api.post("/api/profile/remember", json={"id": event}, headers=AGENT).status_code
        == 401
    )
    assert (
        api.post(
            "/api/profile/remember", json={"id": event}, headers=OPERATOR
        ).status_code
        == 200
    )
    restarted = client(store, layer)
    response = restarted.post("/requests", json=payload, headers=AGENT)
    assert response.json()["decision"] == "auto_rejected"
    assert "receipt" not in response.json()
    restarted.get("/ui/login?key=operator")
    page = restarted.get("/ui/profile")
    assert "Approval profile &amp; history" in page.text
    assert "/ui/approval-assets/profile.js" in page.text
    snapshot = restarted.get("/api/profile").json()
    assert snapshot["rules"][0]["decision"] == "deny"
    assert snapshot["rules"][0]["enabled"] == 1


def test_signature_binds_contract_project_and_code(
    tmp_path: Path, layer: ApprovedLayer
) -> None:
    payload = request(layer)
    project = str(layer.import_roots[0])
    original = verify(payload, layer, project=project)
    changed = request(layer, SOURCE.replace("at_most(1)", "at_most(2)"))
    assert verify(changed, layer, project=project)["signature"] != original["signature"]
    assert (
        verify(payload, layer, project=str(tmp_path))["signature"]
        != original["signature"]
    )
    contracts = layer.trusted_roots[0] / "contracts.py"
    contracts.write_text(contracts.read_text() + "\n# updated trusted library\n")
    assert verify(payload, layer, project=project)["signature"] != original["signature"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("guarantees", ["emails.none()"]),
        ("program_sha256", "forged"),
        ("coverage", [{"status": "covered"}]),
    ],
)
def test_forged_consent_is_never_remembered(
    layer: ApprovedLayer, field: str, value: object
) -> None:
    payload = request(layer)
    payload[field] = value
    with pytest.raises(ValueError, match="independent verification failed"):
        verify(payload, layer, project=str(layer.import_roots[0]))


def test_failed_verification_is_manual_and_unrememberable(
    tmp_path: Path, layer: ApprovedLayer, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProfileStore(tmp_path / "history.sqlite", project=layer.import_roots[0])
    api = client(store, layer)
    payload = request(layer)
    decide(api, payload)

    def timeout(*args: object, **kwargs: object) -> dict[str, object]:
        raise subprocess.TimeoutExpired("verifier", 20)

    monkeypatch.setattr("clauz3.approvals.service.verify_profile", timeout)
    payload["profile_eligible"] = True
    payload["contract_descriptions"] = {
        "emails.at_most(1)": {"text": "Forged sentence"}
    }
    assert api.post("/requests", json=payload, headers=AGENT).status_code == 503
    api.get("/ui/login?key=operator")
    assert "Forged sentence" not in api.get("/ui").text
    response = api.post(
        f"/api/requests/{payload['request_id']}/decision",
        json={"decision": "approved_once", "remember": True},
        headers=OPERATOR,
    )
    assert response.status_code == 400
    assert api.get(f"/requests/{payload['request_id']}").json()["status"] == "pending"


def test_store_deny_wins_and_projects_are_isolated(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "history.sqlite", project=tmp_path / "project")
    basis: dict[str, object] = {"signature": "test"}
    first = store.record({}, basis)
    second = store.record({}, basis)
    store.decide(first, "approved_once", remember=True)
    store.decide(second, "rejected", remember=True)
    match = store.match("test")
    assert match and match["decision"] == "deny"
    store.disable(str(match["id"]))
    assert cast(dict[str, object], store.match("test"))["decision"] == "allow"
    other = ProfileStore(store.path, project=tmp_path / "elsewhere")
    assert other.match("test") is None
    assert not other.snapshot()["history"]
    with pytest.raises(ValueError):
        other.remember(first)


def test_profiles_cannot_override_explicit_policy(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "history.sqlite", project=tmp_path)
    with pytest.raises(ValueError, match="cannot yet be combined"):
        create_approval_app(profile=store, policy=ApprovalPolicy((), ()))


def test_unidentifiable_native_solver_cannot_have_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "clauz3.approvals.profiles.verification.distribution",
        lambda name: SimpleNamespace(files=None),
    )
    with pytest.raises(ValueError, match="cannot identify"):
        _engine_identity()


def test_trusted_documentation_survives_restart_and_escapes_html(
    tmp_path: Path, layer: ApprovedLayer
) -> None:
    contracts = layer.trusted_roots[0] / "contracts.py"
    contracts.write_text(
        contracts.read_text()
        .replace(
            "Guarantee that every sent email targets one of these addresses.",
            'Recipients must be allowed.\n\n    <script>alert("doc")</script>',
        )
        .replace(
            "@contract\ndef at_most",
            '@contract(description="At most {count} email calls are permitted.")\n'
            "def at_most",
        )
    )
    store = ProfileStore(tmp_path / "history.sqlite", project=layer.import_roots[0])
    api = client(store, layer)
    payload = request(layer)
    payload["contract_documentation"] = {
        "emails.at_most(1)": "Forged description: anything is allowed."
    }
    payload["contract_descriptions"] = {
        "emails.at_most(1)": {"text": "Forged sentence"}
    }
    payload["library_mock_declarations"] = {
        "kind": "mock",
        "declared_mock": ["Forged mock declaration"],
    }
    decide(api, payload)
    api.get("/ui/login?key=operator")
    live = api.get("/ui").text
    assert "Recipients must be allowed." in live
    assert "&lt;script&gt;" in live
    assert '<script>alert("doc")</script>' not in live
    assert "Forged description" not in live
    assert "Guarantee that at most count emails are sent." in live
    assert "At most 1 email calls are permitted." in live
    assert "Forged sentence" not in live
    assert "Includes simulated tools" in live
    assert "Forged mock declaration" not in live

    # History uses the approved snapshot, even if the installed docs change.
    contracts.write_text(
        contracts.read_text()
        .replace("Recipients", "CHANGED")
        .replace("At most {count} email calls are permitted.", "CHANGED {count}.")
    )
    restarted = client(store, layer)
    restarted.get("/ui/login?key=operator")
    page = restarted.get("/ui/profile")
    assert "/ui/approval-assets/guarantees.js" in page.text
    assert restarted.get("/ui/approval-assets/guarantees.js").status_code == 200
    snapshot = restarted.get("/api/profile").json()
    history = json.dumps(snapshot)
    assert "Recipients must be allowed." in history
    assert "CHANGED" not in history
    assert "Forged description" not in history
    assert "At most 1 email calls are permitted." in history
    saved = cast(dict[str, object], objects(store.snapshot()["history"])[0]["record"])
    descriptions = cast(dict[str, dict[str, object]], saved["contract_descriptions"])
    assert descriptions["emails.at_most(1)"]["parameters"] == {"count": 1}


def test_legacy_guarantees_render_without_documentation() -> None:
    assert render_guarantees({"guarantees": ["rules.read_only()"]}) == (
        "<li><code>rules.read_only()</code></li>"
    )


def test_manual_service_does_not_trust_submitted_documentation() -> None:
    api = TestClient(create_approval_app(max_wait_seconds=0.01))
    response = api.post(
        "/requests",
        json={
            "request_id": "forged-docs",
            "guarantees": ["rules.read_only()"],
            "contract_documentation": {"rules.read_only()": "Forged description"},
            "contract_descriptions": {"rules.read_only()": {"text": "Forged sentence"}},
        },
    )
    assert response.status_code == 503
    page = api.get("/ui").text
    assert "rules.read_only()" in page
    assert "Forged description" not in page
    assert "Forged sentence" not in page


def test_pending_request_cannot_change_verification_context(
    tmp_path: Path, layer: ApprovedLayer, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProfileStore(tmp_path / "history.sqlite", project=layer.import_roots[0])
    api = client(store, layer)
    payload = request(layer)
    assert api.post("/requests", json=payload, headers=AGENT).status_code == 503

    def changed(*args: object, **kwargs: object) -> dict[str, object]:
        return {"signature": "different verified layer"}

    monkeypatch.setattr("clauz3.approvals.service.verify_profile", changed)
    response = api.post("/requests", json=payload, headers=AGENT)
    assert response.status_code == 409
    assert len(objects(store.snapshot()["history"])) == 1


def test_false_proof_and_unapproved_roots_cannot_use_profile(
    tmp_path: Path, layer: ApprovedLayer
) -> None:
    payload = request(
        layer,
        SOURCE.replace(
            'send_email("bob@example.com",', 'send_email("ceo@example.com",'
        ),
    )
    payload["proofs"] = [{"conclusion": "proved!"}]
    with pytest.raises(ValueError, match="independent verification failed"):
        verify(payload, layer, project=str(layer.import_roots[0]))
    store = ProfileStore(tmp_path / "history.sqlite", project=layer.import_roots[0])
    payload["trusted_roots"] = [str(tmp_path / "untrusted")]
    assert (
        client(store, layer).post("/requests", json=payload, headers=AGENT).status_code
        == 403
    )
    assert not store.snapshot()["history"]


def test_concurrent_promotion_is_idempotent_and_atomic(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "history.sqlite", project=tmp_path)
    event = store.record({}, {"signature": "verified"})
    store.decide(event, "approved_once")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(store.remember, [event] * 8))
    assert len(set(results)) == 1
    assert len(objects(store.snapshot()["rules"])) == 1
    ineligible = store.record({}, None)
    with pytest.raises(ValueError, match="no verified"):
        store.decide(ineligible, "approved_once", remember=True)
    history = objects(store.snapshot()["history"])
    assert (
        next(item for item in history if item["id"] == ineligible)["decision"] is None
    )
