"""Portable authoring examples prove their claims and reject tighter claims."""

import re
import shutil
from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.libraries.install import install_library
from clauz3.libraries.layout import expand_import_roots
from clauz3.prover import prove_text

SKILL = Path(__file__).resolve().parents[1] / "skills/clauz3-authoring"


def materialize(tmp_path: Path, reference: str) -> tuple[Path, str, list[Path]]:
    # Exercise an installed skill directory, without any adjacent docs/ or src/.
    copied = tmp_path / "skills/clauz3-authoring"
    shutil.copytree(SKILL, copied)
    contents = (copied / "references" / reference).read_text()
    project = tmp_path / "consumer"
    entry = ""
    for code in re.findall(r"```python\n(.*?)\n```", contents, re.DOTALL):
        label = code.splitlines()[0].removeprefix("# ")
        assert label.endswith(".py")
        target = project / label
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code + "\n")
        if not label.startswith("tools/"):
            entry = code
    assert entry
    return project, entry, [project / "tools/bio/trusted"]


@pytest.mark.parametrize(
    "replacement,expected",
    [
        (None, Conclusion.OK),
        (("assert len(hits) <= 5", "assert len(hits) <= 4"), Conclusion.FAIL),
        (("queries_at_most(1)", "queries_at_most(0)"), Conclusion.FAIL),
    ],
)
def test_returned_record_example(
    tmp_path: Path, replacement: tuple[str, str] | None, expected: Conclusion
) -> None:
    project, source, roots = materialize(tmp_path, "returned-data.md")
    if replacement:
        source = source.replace(*replacement)
    proofs = prove_text(source, trusted_roots=roots, import_paths=[project])
    assert [p.proof.conclusion for p in proofs] == [expected]


@pytest.mark.parametrize(
    "change,expected",
    [
        ("none", Conclusion.OK),
        ("destination", Conclusion.FAIL),
        ("read_only", Conclusion.FAIL),
    ],
)
def test_shared_filesystem_event_example(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    expected: Conclusion,
) -> None:
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "home"))
    project, source, roots = materialize(tmp_path, "shared-filesystem-events.md")
    install_library("stdlib:filesystem", into=project)
    roots.append(project / ".clauz3/tools/filesystem/trusted")
    if change == "destination":
        source = source.replace(
            'convert_alignment("input.sam", "output.bam")',
            'convert_alignment("input.sam", "elsewhere.bam")',
        )
    elif change == "read_only":
        source = source.replace(
            "def main()", "@clauz3.guarantee(fs.read_only())\ndef main()"
        )
    proofs = prove_text(
        source, trusted_roots=roots, import_paths=expand_import_roots([project])
    )
    assert [p.proof.conclusion for p in proofs] == [expected]
