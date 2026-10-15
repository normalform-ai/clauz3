from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "claude-code-review.yml"
HEAD_SHA = "a" * 40
RUN_MARKER = "<!-- claude-review-run" + ":123:1 -->"
CHECKLIST_ITEMS = (
    "False-OK proof paths assessed",
    "Fail-closed behavior assessed",
    "Trusted/untrusted boundary assessed",
    "Prove and run parity assessed",
    "Regression specificity and load-bearing behavior assessed",
    "Validation adequacy assessed",
)


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _prepare_script() -> str:
    workflow = _workflow_text()
    step_start = workflow.index("      - name: Prepare the formal review payload")
    heredoc = "          python3 - <<'PY'\n"
    script_start = workflow.index(heredoc, step_start) + len(heredoc)
    script_end = workflow.index("\n          PY", script_start)
    return textwrap.dedent(workflow[script_start:script_end])


def _save_script() -> str:
    workflow = _workflow_text()
    step_start = workflow.index("      - name: Save the structured review output")
    heredoc = "          python3 - <<'PY'\n"
    script_start = workflow.index(heredoc, step_start) + len(heredoc)
    script_end = workflow.index("\n          PY", script_start)
    return textwrap.dedent(workflow[script_start:script_end])


def _run_save(
    tmp_path: Path,
    output: str,
    **secrets: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN"):
        env.pop(name, None)
    env.update(secrets)
    env["STRUCTURED_OUTPUT"] = output
    env["STRUCTURED_OUTPUT_FILE"] = str(tmp_path / "out" / "structured-output.json")
    return subprocess.run(
        [sys.executable, "-c", _save_script()],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def _checklist(*, checked: bool = True) -> str:
    mark = "x" if checked else " "
    return "\n".join(f"- [{mark}] {item}" for item in CHECKLIST_ITEMS)


def _payload(
    *,
    body: str | None = None,
    findings: list[dict[str, str]] | None = None,
    review_completed: bool = True,
    max_severity: str | None = None,
) -> dict[str, Any]:
    actual_findings = [] if findings is None else findings
    if max_severity is None:
        max_severity = "NONE" if not actual_findings else actual_findings[0]["severity"]
    return {
        "review_completed": review_completed,
        "max_severity": max_severity,
        "findings": actual_findings,
        "body": _checklist() if body is None else body,
    }


def _run_prepare(
    tmp_path: Path,
    payload: dict[str, Any] | None,
    *,
    raw: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the payload step against a structured-output file.

    ``payload`` is written as JSON; ``raw`` is written verbatim instead; with
    both ``None`` no file is created.
    """

    output = tmp_path / "review.json"
    structured = tmp_path / "structured-output.json"
    if raw is not None:
        structured.write_text(raw, encoding="utf-8")
    elif payload is not None:
        structured.write_text(json.dumps(payload), encoding="utf-8")
    env = os.environ.copy()
    env.pop("STRUCTURED_OUTPUT", None)
    env.update(
        {
            "REVIEW_HEAD_SHA": HEAD_SHA,
            "REVIEW_PAYLOAD": str(output),
            "REVIEW_RUN_MARKER": RUN_MARKER,
            "STRUCTURED_OUTPUT_FILE": str(structured),
        }
    )
    return subprocess.run(
        [sys.executable, "-c", _prepare_script()],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def test_prepare_review_accepts_labeled_checklist(tmp_path: Path) -> None:
    body = "# Automated review\n\n" + _checklist()

    result = _run_prepare(tmp_path, _payload(body=body))

    assert result.returncode == 0, result.stderr
    prepared = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))
    assert prepared == {
        "body": body + "\n\n" + RUN_MARKER,
        "commit_id": HEAD_SHA,
        "event": "APPROVE",
    }


def test_prepare_review_accepts_markdown_decorated_finding_echo(
    tmp_path: Path,
) -> None:
    title = (
        "Bitwise ops with a bool operand return the left operand; "
        "the recorded TypeError is suppressible"
    )
    location = (
        "src/clauz3/_vendor/deal_solver/_proxies/_int.py:551 "
        "(also 562, 573); docs/reference/python-subset.md:222"
    )
    finding = {
        "severity": "IMPORTANT",
        "title": title,
        "location": location,
        "details": "Concrete failure and required fix.",
    }
    body = (
        _checklist()
        + "\n\n### IMPORTANT — **"
        + title
        + "**\n"
        + "**src/clauz3/_vendor/deal_solver/_proxies/_int.py:551** "
        + "(also 562, 573); **docs/reference/python-subset.md:222**"
    )

    result = _run_prepare(
        tmp_path,
        _payload(
            body=body,
            findings=[finding],
            max_severity="IMPORTANT",
        ),
    )

    assert result.returncode == 0, result.stderr
    prepared = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))
    assert prepared["event"] == "REQUEST_CHANGES"


def test_prepare_review_appends_a_finding_the_body_omits(tmp_path: Path) -> None:
    """An un-echoed finding is appended, not grounds for discarding the review.

    The invariant is "every structured finding reaches the reviewer". Enforcing
    it by *rejecting* the whole review made a shortened heading cost a complete
    review (PR #121 lost an 8-finding review, two of them CRITICAL, because one
    heading dropped a trailing clause). Appending from the structured data makes
    the same invariant hold by construction.
    """
    finding = {
        "severity": "SUGGESTION",
        "title": "Actionable finding",
        "location": "src/example.py:10",
        "details": "Concrete failure and required fix.",
    }
    body = _checklist() + "\n\nActionable finding"

    result = _run_prepare(
        tmp_path,
        _payload(
            body=body,
            findings=[finding],
            max_severity="SUGGESTION",
        ),
    )

    assert result.returncode == 0
    assert "1 of 1 findings were not echoed" in result.stdout
    prepared = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))
    for field in ("title", "location", "details"):
        assert finding[field] in prepared["body"]


@pytest.mark.parametrize(
    ("title", "location", "body_echo"),
    [
        (
            "Actionable finding",
            "src/example.py:10",
            "Actionable finding — src/example.py:100",
        ),
        (
            "Missing bound check",
            "src/example.py:10",
            "The missing bound checkpoint — src/example.py:10",
        ),
    ],
)
def test_prepare_review_appends_on_normalized_token_boundary_near_misses(
    tmp_path: Path,
    title: str,
    location: str,
    body_echo: str,
) -> None:
    """Near-misses still do not count as an echo -- they get appended instead.

    ``src/example.py:100`` must not satisfy ``src/example.py:10``, and
    ``checkpoint`` must not satisfy ``check``. The token-boundary matching that
    guarantees this is unchanged; only the consequence of a miss has changed.
    """
    finding = {
        "severity": "SUGGESTION",
        "title": title,
        "location": location,
        "details": "Concrete failure and required fix.",
    }

    result = _run_prepare(
        tmp_path,
        _payload(
            body=_checklist() + "\n\n" + body_echo,
            findings=[finding],
            max_severity="SUGGESTION",
        ),
    )

    assert result.returncode == 0
    assert "1 of 1 findings were not echoed" in result.stdout
    prepared = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))
    assert "Findings appended from structured output" in prepared["body"]


def test_prepare_review_rejects_reserved_marker_inside_a_finding(
    tmp_path: Path,
) -> None:
    """The reserved-value guard must be total over what actually gets posted.

    It used to run over the body only, before anything was appended. Once
    un-echoed findings are appended, a finding field is part of the posted body
    too, so a marker hidden in ``details`` would slip past a check that had
    already passed.
    """
    finding = {
        "severity": "SUGGESTION",
        "title": "Actionable finding",
        "location": "src/example.py:10",
        "details": "Sneaky <!-- claude-review-run:1:1 --> marker.",
    }

    result = _run_prepare(
        tmp_path,
        _payload(
            body=_checklist() + "\n\nActionable finding — src/example.py:10",
            findings=[finding],
            max_severity="SUGGESTION",
        ),
    )

    assert result.returncode != 0
    assert "reserved value" in result.stderr


def test_prepare_review_rejects_punctuation_only_finding(
    tmp_path: Path,
) -> None:
    """Still fatal: this is a malformed finding, not a formatting slip.

    A title and location with no alphanumeric content carry no information, so
    appending them would satisfy the letter of "every finding reaches the
    reviewer" while telling the reviewer nothing. Only *echo* failures are
    recoverable.
    """
    finding = {
        "severity": "SUGGESTION",
        "title": "***",
        "location": ":::",
        "details": "Concrete failure and required fix.",
    }

    result = _run_prepare(
        tmp_path,
        _payload(
            body=_checklist() + "\n\n*** :::",
            findings=[finding],
            max_severity="SUGGESTION",
        ),
    )

    assert result.returncode != 0
    assert "no content" in result.stderr


@pytest.mark.parametrize(
    "body",
    [
        "Leading prose\n" + _checklist(),
        "  # Indented heading\n" + _checklist(),
        "# First heading\n# Second heading\n" + _checklist(),
        "> " + _checklist(),
    ],
)
def test_prepare_review_rejects_noncanonical_checklist_prefix(
    tmp_path: Path,
    body: str,
) -> None:
    result = _run_prepare(tmp_path, _payload(body=body))

    assert result.returncode != 0
    assert "checklist" in result.stderr, result.stderr


def test_prepare_review_rejects_completion_mismatch(tmp_path: Path) -> None:
    result = _run_prepare(
        tmp_path,
        _payload(body=_checklist(checked=False), review_completed=True),
    )

    assert result.returncode != 0
    assert "completion does not match" in result.stderr


def test_prepare_review_reserves_marker_namespace(tmp_path: Path) -> None:
    forged_marker = "<!-- claude-review-run" + ":forged:9 -->"
    body = _checklist() + "\n\n" + forged_marker

    result = _run_prepare(tmp_path, _payload(body=body))

    assert result.returncode != 0
    assert "reserved value" in result.stderr


def test_prepare_review_accepts_schema_boundaries(tmp_path: Path) -> None:
    finding = {
        "severity": "SUGGESTION",
        "title": "t" * 160,
        "location": "p" * 300,
        "details": "d" * 2_800,
    }
    findings = [finding.copy() for _ in range(10)]
    body_prefix = (
        _checklist() + "\n\n" + finding["title"] + "\n" + finding["location"] + "\n"
    )
    body = body_prefix + ("b" * (14_000 - len(body_prefix)))

    result = _run_prepare(
        tmp_path,
        _payload(
            body=body,
            findings=findings,
            max_severity="SUGGESTION",
        ),
    )

    assert len(body) == 14_000
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "case",
    [
        "findings",
        "title",
        "location",
        "details",
        "body",
    ],
)
def test_prepare_review_rejects_consumer_limit_overflow(
    tmp_path: Path,
    case: str,
) -> None:
    finding = {
        "severity": "SUGGESTION",
        "title": "title",
        "location": "path.py:1",
        "details": "details",
    }
    findings = [finding]
    if case == "findings":
        findings = [finding.copy() for _ in range(11)]
    elif case == "title":
        finding["title"] = "t" * 201
    elif case == "location":
        finding["location"] = "p" * 401
    elif case == "details":
        finding["details"] = "d" * 3_001
    body = _checklist() + "\n\n" + finding["title"] + "\n" + finding["location"]
    if case == "body":
        body += "b" * 15_001

    result = _run_prepare(
        tmp_path,
        _payload(
            body=body,
            findings=findings,
            max_severity="SUGGESTION",
        ),
    )

    assert result.returncode != 0
    if case in {"title", "location", "details"}:
        assert "oversized field" in result.stderr


def test_schema_limits_leave_consumer_headroom() -> None:
    match = re.search(r"--json-schema '([^'\n]+)'", _workflow_text())
    assert match is not None
    schema = json.loads(match.group(1))
    properties = schema["properties"]
    finding_properties = properties["findings"]["items"]["properties"]

    assert properties["findings"]["maxItems"] == 10
    assert finding_properties["title"]["maxLength"] == 160
    assert finding_properties["location"]["maxLength"] == 300
    assert finding_properties["details"]["maxLength"] == 2_800
    assert properties["body"]["maxLength"] == 14_000
    assert finding_properties["title"]["maxLength"] < 200
    assert finding_properties["location"]["maxLength"] < 400
    assert finding_properties["details"]["maxLength"] < 3_000
    assert properties["body"]["maxLength"] < 15_000


@pytest.mark.parametrize("raw", [None, "", "  \n\t "])
def test_prepare_review_rejects_missing_or_empty_output(
    tmp_path: Path,
    raw: str | None,
) -> None:
    result = _run_prepare(tmp_path, None, raw=raw)

    assert result.returncode != 0
    assert "Structured review output is empty or missing" in result.stderr
    assert not (tmp_path / "review.json").exists()


def test_prepare_review_rejects_malformed_output(tmp_path: Path) -> None:
    result = _run_prepare(tmp_path, None, raw="{not json")

    assert result.returncode != 0
    assert "Invalid structured review output" in result.stderr
    assert not (tmp_path / "review.json").exists()


def test_save_review_writes_clean_output(tmp_path: Path) -> None:
    text = json.dumps(_payload())

    result = _run_save(tmp_path, text, GH_TOKEN="ghs_example_token_value")

    assert result.returncode == 0, result.stdout + result.stderr
    saved = tmp_path / "out" / "structured-output.json"
    assert saved.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("encode", [False, True])
@pytest.mark.parametrize(
    "name", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN"]
)
def test_save_review_withholds_output_containing_a_credential(
    tmp_path: Path,
    name: str,
    encode: bool,
) -> None:
    secret = "credential-value-1234567890"
    leaked = base64.b64encode(secret.encode()).decode() if encode else secret
    text = json.dumps(_payload(body=_checklist() + "\n\n" + leaked))

    result = _run_save(tmp_path, text, **{name: secret})

    assert result.returncode != 0
    assert f"contains {name}" in result.stdout
    assert secret not in result.stdout + result.stderr
    assert not (tmp_path / "out" / "structured-output.json").exists()


@pytest.mark.parametrize("output", ["", "  \n"])
def test_save_review_rejects_empty_output(tmp_path: Path, output: str) -> None:
    result = _run_save(tmp_path, output)

    assert result.returncode != 0
    assert "produced no structured output" in result.stdout
    assert not (tmp_path / "out" / "structured-output.json").exists()
