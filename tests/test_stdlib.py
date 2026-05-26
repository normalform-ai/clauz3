from pathlib import Path

import pytest

from clauz3.cli import main
from clauz3.install import InstallError, available_stdlib_tools, install_layer
from clauz3.prover import ProofResult, prove_path

ROOT = Path(__file__).resolve().parents[1]
STDLIB = ROOT / "src/clauz3/stdlib"
FS_ROOT = STDLIB / "filesystem"
FS_TRUSTED = FS_ROOT / "tools/filesystem/trusted"
GREP_ROOT = STDLIB / "grep"
GREP_TRUSTED = GREP_ROOT / "tools/grep/trusted"


def _prove(
    tmp_path: Path, source: str, *, import_roots: list[Path], trusted: Path
) -> list[ProofResult]:
    case = tmp_path / "case.py"
    case.write_text(source)
    return prove_path(case, trusted_roots=[trusted], import_roots=import_roots)


def test_filesystem_only_write_under_proves(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.filesystem.trusted import contracts as fs\n"
        "from tools.filesystem.trusted.effects import write_file\n"
        "\n"
        '@clauz3.guarantee(fs.only_write_under("/sandbox"))\n'
        "def main() -> None:\n"
        '    write_file("/sandbox/out.txt", "hi")\n'
    )
    results = _prove(tmp_path, source, import_roots=[FS_ROOT], trusted=FS_TRUSTED)
    assert [r.ok for r in results] == [True]


def test_filesystem_only_write_under_rejects_escape(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.filesystem.trusted import contracts as fs\n"
        "from tools.filesystem.trusted.effects import write_file\n"
        "\n"
        '@clauz3.guarantee(fs.only_write_under("/sandbox"))\n'
        "def main() -> None:\n"
        '    write_file("/etc/passwd", "pwned")\n'
    )
    results = _prove(tmp_path, source, import_roots=[FS_ROOT], trusted=FS_TRUSTED)
    assert [r.ok for r in results] == [False]


def test_grep_is_governed_by_filesystem_read_policy(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.grep.trusted import contracts as grep_rules\n"
        "from tools.grep.trusted.effects import grep\n"
        "\n"
        '@clauz3.guarantee(grep_rules.only_read_under("/repo"))\n'
        "def main() -> None:\n"
        '    grep("TODO", "/repo/src/app.py")\n'
    )
    results = _prove(
        tmp_path, source, import_roots=[GREP_ROOT, FS_ROOT], trusted=GREP_TRUSTED
    )
    assert [r.ok for r in results] == [True]


def test_grep_read_outside_root_rejected(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.grep.trusted import contracts as grep_rules\n"
        "from tools.grep.trusted.effects import grep\n"
        "\n"
        '@clauz3.guarantee(grep_rules.only_read_under("/repo"))\n'
        "def main() -> None:\n"
        '    grep("secret", "/etc/shadow")\n'
    )
    results = _prove(
        tmp_path, source, import_roots=[GREP_ROOT, FS_ROOT], trusted=GREP_TRUSTED
    )
    assert [r.ok for r in results] == [False]


def test_available_stdlib_tools_lists_bundled_tools() -> None:
    tools = available_stdlib_tools()
    assert "filesystem" in tools
    assert "grep" in tools


def test_install_stdlib_scheme(tmp_path: Path) -> None:
    result = install_layer("stdlib:filesystem", into=tmp_path)

    assert result.domains == ["filesystem"]
    copied = tmp_path / "tools/filesystem/trusted/effects.py"
    assert copied.is_file()
    assert "read_file" in copied.read_text()


def test_install_stdlib_bare_name(tmp_path: Path) -> None:
    result = install_layer("grep", into=tmp_path)

    assert result.domains == ["grep"]
    assert (tmp_path / "tools/grep/trusted/contracts.py").is_file()


def test_install_unknown_stdlib_tool_lists_available(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="stdlib:filesystem"):
        install_layer("stdlib:nope", into=tmp_path)


def test_install_stdlib_cli(tmp_path: Path) -> None:
    assert main(["install", "stdlib:filesystem", "--into", str(tmp_path)]) == 0
    assert (tmp_path / "tools/filesystem/trusted/effects.py").is_file()
