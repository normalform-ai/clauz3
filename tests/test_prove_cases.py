from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from clauz3._prove_cases import main

PROVES = "def main() -> int:\n    return 1\n"
FAILS = "def main() -> int:\n    assert False\n    return 1\n"
REFUSED = "import helpers\n\n\ndef main() -> None:\n    pass\n"


def _write(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / name
    path.write_text(source)
    return str(path)


def test_cases_matching_their_names_pass(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    cases = [
        _write(tmp_path, "a_pass.py", PROVES),
        _write(tmp_path, "b_fail.py", FAILS),
        # A configuration error is "does not prove", as `if prove; then exit 1`
        # treated it.
        _write(tmp_path, "c_fail.py", REFUSED),
    ]

    assert main(cases) == 0
    out = capsys.readouterr().out
    assert f"{cases[0]}: main: proved!" in out
    assert f"{cases[1]}: main: failed assertion" in out
    assert out.rstrip().endswith("all 3 cases matched their names")


def test_a_case_that_contradicts_its_name_fails_the_run(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    should_prove = _write(tmp_path, "wrong_pass.py", FAILS)
    should_fail = _write(tmp_path, "wrong_fail.py", PROVES)
    fine = _write(tmp_path, "fine_pass.py", PROVES)

    assert main([should_prove, fine, should_fail]) == 1
    out = capsys.readouterr().out
    assert "2 of 3 cases did not match their names" in out
    assert f"{should_prove}: expected it to prove" in out
    assert f"{should_fail}: expected it to fail" in out
    assert f"{fine}: expected" not in out


def test_a_case_without_an_expectation_suffix_is_refused(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    unnamed = _write(tmp_path, "case.py", PROVES)
    named = _write(tmp_path, "case_pass.py", PROVES)

    assert main([named, unnamed]) == 2
    captured = capsys.readouterr()
    assert f"{unnamed}: name must end in _pass.py or _fail.py" in captured.err
    # Nothing is proved when the batch is malformed.
    assert captured.out == ""


@pytest.mark.parametrize(
    "missing",
    [
        pytest.param("typo_fail.py", id="typo"),
        # The shell passes a glob that matched nothing through literally.
        pytest.param("*_fail.py", id="unmatched-glob"),
        pytest.param("typo_pass.py", id="pass-suffix"),
    ],
)
def test_a_missing_case_is_refused(
    tmp_path: Path, capsys: CaptureFixture[str], missing: str
) -> None:
    named = _write(tmp_path, "case_pass.py", PROVES)
    absent = str(tmp_path / missing)

    assert main([named, absent]) == 2
    captured = capsys.readouterr()
    assert f"{absent}: no such file" in captured.err
    # Nothing is proved when the batch is malformed.
    assert captured.out == ""
