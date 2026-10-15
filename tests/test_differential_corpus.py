"""Opt-in corpus sweep, run separately from the ordinary pytest suite."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS_TIMEOUT_SECONDS = 30 * 60


@pytest.mark.differential_corpus
def test_corpus_differential_sweep(tmp_path: Path) -> None:
    # Prover tests retain trusted images and registration history. A fresh
    # process prevents this sweep's cost from depending on prior test order.
    report_path = tmp_path / "differential-report.json"
    command = [
        sys.executable,
        "-m",
        "clauz3.differential",
        "--root",
        str(ROOT),
        "--runs",
        "4",
        "--max-rows",
        "2",
        "--timeout-ms",
        "10000",
        "--verbose",
        "--json",
        str(report_path),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            timeout=CORPUS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"Differential corpus exceeded {CORPUS_TIMEOUT_SECONDS}s. "
            "The last stderr progress line identifies the last completed case.\n"
            f"Report (written on completion): {report_path}",
            pytrace=False,
        )

    # Inherit output so `just differential-test` streams per-case progress.
    # Normal pytest capture still includes all CLI diagnostics on failure.
    print(f"Differential report: {report_path}")
    assert completed.returncode == 0, (
        f"Differential CLI exited {completed.returncode}; see captured output "
        f"and report {report_path}"
    )
    assert report_path.is_file(), "Differential CLI did not write its JSON report"
    summary = json.loads(report_path.read_text())["summary"]
    print(f"Report summary: {summary}")
    # Guard against excluding the useful corpus or returning only empty rows.
    assert summary["statuses"].get("checked", 0) >= 100, summary
    assert summary["runs"] >= 100, summary
    assert summary["nonempty_queries"] >= 5, summary
