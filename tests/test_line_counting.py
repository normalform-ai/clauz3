"""Line counting remains an approved read with explicit text semantics."""

from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text

ROOT = Path(__file__).resolve().parents[1]
FS = ROOT / "src/clauz3/stdlib/filesystem"
TRUSTED = FS / "tools/filesystem/trusted"


def source(expression: str, contract: str = 'fs.only_read_under("./data/")') -> str:
    return (
        "import clauz3\n"
        "from tools.filesystem.trusted.effects import "
        "read_file, read_lines, write_file\n"
        "from tools.filesystem.trusted import contracts as fs\n"
        f"@clauz3.guarantee({contract})\n"
        "def main() -> None:\n"
        f"    print({expression})\n"
    )


def conclusion(text: str) -> Conclusion:
    proofs = prove_text(text, import_paths=[FS], trusted_roots=[TRUSTED])
    assert len(proofs) == 1
    return proofs[0].proof.conclusion


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("hello", ["hello"]),
        ("hello\n", ["hello"]),
        ("hello\n\n", ["hello", ""]),
        ("\n", [""]),
        ("one\r\n\r\nthree", ["one", "", "three"]),
        ("one\rtwo", ["one", "two"]),
        ("Ω\u2028😀\u0085end", ["Ω", "😀", "end"]),
    ],
)
def test_read_lines_runtime(
    text: str, expected: list[str], tmp_path: Path, filesystem_effects: ModuleType
) -> None:
    path = tmp_path / "input.txt"
    path.write_bytes(text.encode("utf-8"))
    assert cast(list[str], filesystem_effects.read_lines(str(path))) == expected


def test_read_lines_decode_failure_is_not_silently_skipped(
    tmp_path: Path, filesystem_effects: ModuleType
) -> None:
    path = tmp_path / "binary"
    path.write_bytes(b"\xff\n")
    with pytest.raises(UnicodeDecodeError):
        filesystem_effects.read_lines(str(path))


@pytest.mark.parametrize("reader", ["read_file", "read_lines"])
@pytest.mark.parametrize(
    ("path", "contract", "expected"),
    [
        ("./data/input.txt", 'fs.only_read_under("./data/")', Conclusion.OK),
        ("./outside/input.txt", 'fs.only_read_under("./data/")', Conclusion.FAIL),
        ("./data/input.txt", "fs.no_reads()", Conclusion.FAIL),
        ("./data/input.txt", 'fs.never_read_under("./data/")', Conclusion.FAIL),
        ("./other/input.txt", 'fs.never_read_under("./data/")', Conclusion.OK),
        ("./data/../secret", "fs.read_only()", Conclusion.FAIL),
        ("", "fs.read_only()", Conclusion.FAIL),
    ],
)
def test_read_contracts_cover_both_effects(
    reader: str, path: str, contract: str, expected: Conclusion
) -> None:
    assert conclusion(source(f"len({reader}({path!r}))", contract)) == expected


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("", ""),
        ("abc", ""),
        ("hello", "\n"),
        ("hello\n\n", "\n"),
        ("aaaaa", "aa"),
        ("abababa", "aba"),
        ("abc", "longer"),
        ("Ω😀Ω\0\ud800\U0010ffff", "Ω"),
        ("\ud800\ud800", "\ud800"),
    ],
)
def test_count_concrete_python_parity(text: str, needle: str) -> None:
    expected = text.count(needle)
    program = (
        "def main() -> None:\n"
        f"    assert {ascii(text)}.count({ascii(needle)}) == {expected}\n"
    )
    assert conclusion(program) == Conclusion.OK
    execute(program, source_name="count.py", policy=build_policy())
    assert (
        conclusion(program.replace(f"== {expected}", f"== {expected + 1}"))
        == Conclusion.FAIL
    )


@pytest.mark.parametrize(
    "assertion",
    [
        's.count("\\n") >= 0',
        's.count("\\n") <= len(s)',
        's.count("") == len(s) + 1',
        's.count("aa") * 2 <= len(s)',
        '(s.count("abc") == 0) == ("abc" not in s)',
        "s.count(needle) == s.count(needle)",
    ],
)
def test_count_symbolic_bounds(assertion: str) -> None:
    assert (
        conclusion(f"def main(s: str, needle: str) -> None:\n    assert {assertion}\n")
        == Conclusion.OK
    )


@pytest.mark.parametrize(
    "expression",
    [
        "s.count()",
        "s.count(1)",
        's.count("x", 1)',
        's.count("x", 0, 2)',
        's.count(sub="x")',
        's.count("x", ctx=1)',
        's.count.__call__("x", start=0)',
    ],
)
def test_count_unsupported_arguments_fail_closed(expression: str) -> None:
    assert (
        conclusion(f"def main(s: str) -> None:\n    print({expression})\n")
        == Conclusion.SKIP
    )


def test_count_cannot_hide_a_forbidden_write() -> None:
    program = source('read_file("./data/input.txt").count("\\n")', "fs.read_only()")
    program += (
        '    if read_file("./data/input.txt").count("\\n") > 0:\n'
        '        write_file("./data/output", "bad")\n'
    )
    assert conclusion(program) == Conclusion.FAIL


def test_count_prints_real_runtime_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/input.txt").write_bytes(b"hello\n\nlast")
    program = source('read_file("./data/input.txt").count("\\n")', "fs.read_only()")
    assert conclusion(program) == Conclusion.OK
    execute(
        program,
        source_name="count.py",
        policy=build_policy(trusted_roots=[TRUSTED], import_roots=[FS]),
        import_roots=[FS],
    )
    assert capsys.readouterr().out == "2\n"


def test_body_diagnostic_names_offending_call_and_line() -> None:
    proofs = prove_text('def main() -> None:\n    print("hello".splitlines())\n')
    proof = proofs[0].proof
    assert proof.conclusion == Conclusion.SKIP
    assert proof.description == "unsupported program body"
    assert "line 2" in str(proof.error)
    # The attribute-surface guard refuses the method reference before the call.
    assert "attribute is outside the interpreter surface" in str(proof.error)
    assert "'hello'.splitlines" in str(proof.error)


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("", ""),
        ("", "a"),
        ("a", ""),
        ("aaaaa", "aa"),
        ("abababa", "aba"),
        ("a\n\n", "\n"),
        ("Ω😀Ω", "Ω"),
        ("\ud800\ud800", "\ud800"),
        ("short", "longer"),
    ],
)
def test_symbolic_count_model_admits_python_result(text: str, needle: str) -> None:
    import z3

    from clauz3._vendor.deal_solver._context import Context
    from clauz3._vendor.deal_solver._proxies import StrSort
    from clauz3._vendor.deal_solver._strings import string_val

    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    symbolic_text = StrSort.var(name="text", ctx=z3.main_ctx())
    symbolic_needle = StrSort.var(name="needle", ctx=z3.main_ctx())
    count = symbolic_text.r_count(symbolic_needle, ctx=ctx)
    solver = z3.Solver()
    solver.set(timeout=2000)
    solver.add(*(term.expr for term in ctx.given))
    solver.add(symbolic_text.expr == string_val(text))
    solver.add(symbolic_needle.expr == string_val(needle))
    solver.add(count.expr == text.count(needle))
    assert solver.check() == z3.sat


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ('len(read_lines("./data/input.txt"))', "3\n"),
        ('read_file("./data/input.txt").count("\\n")', "2\n"),
    ],
)
def test_line_count_through_authenticated_approval_service(
    expression: str,
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from collections.abc import Mapping

    from fastapi.testclient import TestClient

    from clauz3.approvals.authority import ApprovedLayer
    from clauz3.approvals.client import ApprovalResponse
    from clauz3.approvals.profiles.store import ProfileStore
    from clauz3.approvals.receipts import (
        SIGNING_KEY_ENV,
        VERIFY_KEY_ENV,
        generate_keypair,
    )
    from clauz3.approvals.service import create_approval_app
    from clauz3.libraries.install import install_layer
    from clauz3.libraries.layout import expand_import_roots
    from clauz3.runner import run_source

    monkeypatch.chdir(tmp_path)
    install_layer("stdlib:filesystem", into=tmp_path)
    roots = [tmp_path / ".clauz3/tools/filesystem/trusted"]
    (tmp_path / "data").mkdir()
    (tmp_path / "data/input.txt").write_bytes(b"hello\n\nlast")
    signing, verifying = generate_keypair()
    monkeypatch.setenv(SIGNING_KEY_ENV, signing)
    monkeypatch.setenv(VERIFY_KEY_ENV, verifying)
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "cache"))
    api = TestClient(
        create_approval_app(
            auth_token="agent",
            decision_token="operator",
            max_wait_seconds=0.01,
            authorized_layers=(
                ApprovedLayer(tuple(roots), tuple(expand_import_roots([tmp_path]))),
            ),
            profile=ProfileStore(tmp_path / "profile.sqlite", project=tmp_path),
        )
    )
    agent = {"Authorization": "Bearer agent"}
    operator = {"Authorization": "Bearer operator"}

    def submit(
        url: str, payload: Mapping[str, object], **kwargs: object
    ) -> ApprovalResponse:
        # Adapt the transport to TestClient; use the actual service's proof
        # verification, pending queue, operator decision and receipt signing.
        pending = api.post("/requests", json=dict(payload), headers=agent)
        assert pending.status_code == 503
        request_id = str(payload["request_id"])
        record = api.get(f"/requests/{request_id}", headers=agent).json()
        assert record["status"] == "pending"
        assert record["profile_eligible"] is True
        assert capsys.readouterr().out == ""
        assert (
            api.post(
                f"/api/requests/{request_id}/decision",
                json={"decision": "approved_once"},
                headers=agent,
            ).status_code
            == 401
        )
        approved = api.post(
            f"/api/requests/{request_id}/decision",
            json={"decision": "approved_once"},
            headers=operator,
        )
        assert approved.status_code == 200
        result = approved.json()
        assert result["receipt"]
        return ApprovalResponse(
            decision=result["decision"],
            request_id=request_id,
            receipt=result["receipt"],
        )

    monkeypatch.setattr("clauz3.runner.submit_approval_request", submit)
    outcome = run_source(
        source(expression),
        source_name="line_count.py",
        trusted_roots=roots,
        import_roots=expand_import_roots([tmp_path]),
        approval_service_url="http://testserver",
    )
    assert outcome.approval.approved
    assert capsys.readouterr().out == expected


@pytest.mark.parametrize(
    ("mapped", "expected"),
    [
        ({"source_kind": "submitted", "line": 42}, "at line 42:"),
        ({"source_kind": "generated_proof"}, "in generated_proof:"),
    ],
)
def test_body_diagnostic_uses_proof_source_mapping(
    mapped: dict[str, object],
    expected: str,
) -> None:
    from clauz3.diagnostics.proof import source_scope
    from clauz3.prover import AgentTheorem

    theorem = next(
        AgentTheorem.from_text('def main() -> None:\n    print("hello".splitlines())\n')
    )
    with source_scope(theorem._func.root(), {(2, 10): mapped}):
        proof = theorem.prove()
    assert proof.conclusion == Conclusion.SKIP
    assert expected in str(proof.error)
    assert "at line 2:" not in str(proof.error)
