from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.cli import main
from clauz3.libraries.install import InstallError, available_stdlib_tools, install_layer
from clauz3.prover import ProofResult, prove_path

ROOT = Path(__file__).resolve().parents[1]
STDLIB = ROOT / "src/clauz3/stdlib"
FS_ROOT = STDLIB / "filesystem"
FS_TRUSTED = FS_ROOT / "tools/filesystem/trusted"
GREP_ROOT = STDLIB / "grep"
GREP_TRUSTED = GREP_ROOT / "tools/grep/trusted"
GIT_ROOT = STDLIB / "git"
GIT_TRUSTED = GIT_ROOT / "tools/git/trusted"
GH_ROOT = STDLIB / "gh"
GH_TRUSTED = GH_ROOT / "tools/gh/trusted"
FIND_ROOT = STDLIB / "find"
FIND_TRUSTED = FIND_ROOT / "tools/find/trusted"
RSYNC_ROOT = STDLIB / "rsync"
RSYNC_TRUSTED = RSYNC_ROOT / "tools/rsync/trusted"
COREUTILS_ROOT = STDLIB / "coreutils"
COREUTILS_TRUSTED = COREUTILS_ROOT / "tools/coreutils/trusted"
ENV_ROOT = STDLIB / "env"
ENV_TRUSTED = ENV_ROOT / "tools/env/trusted"


def _prove(
    tmp_path: Path,
    source: str,
    *,
    import_roots: list[Path],
    trusted: Path | list[Path],
) -> list[ProofResult]:
    case = tmp_path / "case.py"
    case.write_text(source)
    roots = trusted if isinstance(trusted, list) else [trusted]
    return prove_path(case, trusted_roots=roots, import_roots=import_roots)


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
    assert [r.proof.conclusion for r in results] == [Conclusion.OK]


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
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


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
    assert [r.proof.conclusion for r in results] == [Conclusion.OK]


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
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


def test_git_read_only_proves(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.git.trusted import contracts as git_rules\n"
        "from tools.git.trusted.effects import git\n"
        "\n"
        "@clauz3.guarantee(git_rules.read_only())\n"
        "def main() -> None:\n"
        '    git("status", [], "/repo")\n'
    )
    results = _prove(tmp_path, source, import_roots=[GIT_ROOT], trusted=GIT_TRUSTED)
    assert [r.proof.conclusion for r in results] == [Conclusion.OK]


def test_git_read_only_rejects_mutation(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.git.trusted import contracts as git_rules\n"
        "from tools.git.trusted.effects import git\n"
        "\n"
        "@clauz3.guarantee(git_rules.read_only())\n"
        "def main() -> None:\n"
        '    git("commit", ["-m", "x"], "/repo")\n'
    )
    results = _prove(tmp_path, source, import_roots=[GIT_ROOT], trusted=GIT_TRUSTED)
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


def test_git_only_push_to_branch_rejects_other_branch(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.git.trusted import contracts as git_rules\n"
        "from tools.git.trusted.effects import git_push\n"
        "\n"
        '@clauz3.guarantee(git_rules.only_push_to_branch("main"))\n'
        "def main() -> None:\n"
        '    git_push("origin", "wip", "/repo")\n'
    )
    results = _prove(tmp_path, source, import_roots=[GIT_ROOT], trusted=GIT_TRUSTED)
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


def test_gh_only_repo_proves(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.gh.trusted import contracts as gh_rules\n"
        "from tools.gh.trusted.effects import gh\n"
        "\n"
        '@clauz3.guarantee(gh_rules.only_repo("cmungall/agent-deal"))\n'
        "def main() -> None:\n"
        '    gh("pr", "list", [], "cmungall/agent-deal")\n'
    )
    results = _prove(tmp_path, source, import_roots=[GH_ROOT], trusted=GH_TRUSTED)
    assert [r.proof.conclusion for r in results] == [Conclusion.OK]


def test_gh_read_only_rejects_merge(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.gh.trusted import contracts as gh_rules\n"
        "from tools.gh.trusted.effects import gh\n"
        "\n"
        "@clauz3.guarantee(gh_rules.read_only())\n"
        "def main() -> None:\n"
        '    gh("pr", "merge", ["42"], "cmungall/agent-deal")\n'
    )
    results = _prove(tmp_path, source, import_roots=[GH_ROOT], trusted=GH_TRUSTED)
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


def test_find_is_governed_by_filesystem_read_policy(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.find.trusted import contracts as find_rules\n"
        "from tools.find.trusted.effects import find\n"
        "\n"
        '@clauz3.guarantee(find_rules.only_read_under("/repo"))\n'
        "def main() -> None:\n"
        '    find("/repo/src", ["-name", "*.py"])\n'
    )
    results = _prove(
        tmp_path, source, import_roots=[FIND_ROOT, FS_ROOT], trusted=FIND_TRUSTED
    )
    assert [r.proof.conclusion for r in results] == [Conclusion.OK]


def test_find_read_outside_root_rejected(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.find.trusted import contracts as find_rules\n"
        "from tools.find.trusted.effects import find\n"
        "\n"
        '@clauz3.guarantee(find_rules.only_read_under("/repo"))\n'
        "def main() -> None:\n"
        '    find("/etc", ["-name", "*.conf"])\n'
    )
    results = _prove(
        tmp_path, source, import_roots=[FIND_ROOT, FS_ROOT], trusted=FIND_TRUSTED
    )
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


def test_rsync_no_delete_proves(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.rsync.trusted import contracts as rsync_rules\n"
        "from tools.rsync.trusted.effects import rsync\n"
        "\n"
        "@clauz3.guarantee(rsync_rules.no_delete())\n"
        "def main() -> None:\n"
        '    rsync("/repo/build/", "/backup/build/", False, ["-a"])\n'
    )
    results = _prove(tmp_path, source, import_roots=[RSYNC_ROOT], trusted=RSYNC_TRUSTED)
    assert [r.proof.conclusion for r in results] == [Conclusion.OK]


def test_rsync_no_delete_rejects_delete(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.rsync.trusted import contracts as rsync_rules\n"
        "from tools.rsync.trusted.effects import rsync\n"
        "\n"
        "@clauz3.guarantee(rsync_rules.no_delete())\n"
        "def main() -> None:\n"
        '    rsync("/repo/build/", "/backup/build/", True, ["-a"])\n'
    )
    results = _prove(tmp_path, source, import_roots=[RSYNC_ROOT], trusted=RSYNC_TRUSTED)
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


def test_coreutils_never_delete_under_proves(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.coreutils.trusted import contracts as cu\n"
        "from tools.coreutils.trusted.effects import remove\n"
        "\n"
        '@clauz3.guarantee(cu.never_delete_under("/etc"))\n'
        "def main() -> None:\n"
        '    remove("/repo/build/cache")\n'
    )
    results = _prove(
        tmp_path, source, import_roots=[COREUTILS_ROOT], trusted=COREUTILS_TRUSTED
    )
    assert [r.proof.conclusion for r in results] == [Conclusion.OK]


def test_coreutils_never_delete_under_rejects_etc(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.coreutils.trusted import contracts as cu\n"
        "from tools.coreutils.trusted.effects import remove\n"
        "\n"
        '@clauz3.guarantee(cu.never_delete_under("/etc"))\n'
        "def main() -> None:\n"
        '    remove("/etc/passwd")\n'
    )
    results = _prove(
        tmp_path, source, import_roots=[COREUTILS_ROOT], trusted=COREUTILS_TRUSTED
    )
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


def test_coreutils_only_modes_rejects_world_writable(tmp_path: Path) -> None:
    source = (
        "import clauz3\n"
        "from tools.coreutils.trusted import contracts as cu\n"
        "from tools.coreutils.trusted.effects import chmod\n"
        "\n"
        "@clauz3.guarantee(cu.only_modes([0o644, 0o755]))\n"
        "def main() -> None:\n"
        '    chmod("/repo/build/run.sh", 0o777)\n'
    )
    results = _prove(
        tmp_path, source, import_roots=[COREUTILS_ROOT], trusted=COREUTILS_TRUSTED
    )
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


def test_available_stdlib_tools_lists_bundled_tools() -> None:
    tools = available_stdlib_tools()
    assert "db" in tools
    assert "filesystem" in tools
    assert "grep" in tools
    assert "git" in tools
    assert "gh" in tools
    assert "find" in tools
    assert "rsync" in tools
    assert "coreutils" in tools


def test_install_stdlib_scheme(tmp_path: Path) -> None:
    result = install_layer("stdlib:filesystem", into=tmp_path)

    assert result.domains == ["filesystem"]
    copied = tmp_path / ".clauz3/tools/filesystem/trusted/effects.py"
    assert copied.is_file()
    assert "read_file" in copied.read_text()


def test_install_stdlib_bare_name(tmp_path: Path) -> None:
    result = install_layer("grep", into=tmp_path)

    assert result.domains == ["grep"]
    assert (tmp_path / ".clauz3/tools/grep/trusted/contracts.py").is_file()


def test_install_unknown_stdlib_tool_lists_available(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="stdlib:filesystem"):
        install_layer("stdlib:nope", into=tmp_path)


def test_install_stdlib_cli(tmp_path: Path) -> None:
    assert main(["install", "stdlib:filesystem", "--into", str(tmp_path)]) == 0
    assert (tmp_path / ".clauz3/tools/filesystem/trusted/effects.py").is_file()


# ── the reach of a policy, after effects became addressable by name ──────────
#
# A relation covered whatever functions carried a ``@deal.has`` marker, so one
# layer's policy silently reached another layer's effects: every write effect
# declared ``write``, so ``fs.only_write_under`` governed ``coreutils.make_dir``
# and ``editor.edit_file`` too. A relation names the functions it covers now,
# which makes each of those a deliberate line in the layer that wants it --- and
# narrows the guarantees below for a program that is not updated.
#
# These pin the narrowing as a recorded result: the filesystem policy proving
# is the *weaker* conclusion, and the sibling test shows which contract to state
# instead.


def test_filesystem_write_policy_no_longer_reaches_coreutils(tmp_path: Path) -> None:
    """``fs.only_write_under`` covers ``write_file``, not ``make_dir``."""

    source = (
        "import clauz3\n"
        "from tools.filesystem.trusted import contracts as fs\n"
        "from tools.coreutils.trusted.effects import make_dir\n"
        "\n"
        '@clauz3.guarantee(fs.only_write_under("/repo/build"))\n'
        "def main() -> None:\n"
        '    make_dir("/etc/cron.d")\n'
    )
    results = _prove(
        tmp_path,
        source,
        import_roots=[FS_ROOT, COREUTILS_ROOT],
        trusted=[FS_TRUSTED, COREUTILS_TRUSTED],
    )
    assert [r.proof.conclusion for r in results] == [Conclusion.OK], (
        "the filesystem policy is silent here"
    )


def test_coreutils_create_policy_catches_what_filesystem_no_longer_does(
    tmp_path: Path,
) -> None:
    """The contract to state instead: coreutils governs its own creations."""

    source = (
        "import clauz3\n"
        "from tools.coreutils.trusted import contracts as cu\n"
        "from tools.coreutils.trusted.effects import make_dir\n"
        "\n"
        '@clauz3.guarantee(cu.only_create_under("/repo/build"))\n'
        "def main() -> None:\n"
        '    make_dir("/etc/cron.d")\n'
    )
    results = _prove(
        tmp_path, source, import_roots=[COREUTILS_ROOT], trusted=COREUTILS_TRUSTED
    )
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


def test_filesystem_read_policy_no_longer_reaches_grep(tmp_path: Path) -> None:
    """``fs.only_read_under`` covers ``read_file``, not ``grep``."""

    source = (
        "import clauz3\n"
        "from tools.filesystem.trusted import contracts as fs\n"
        "from tools.grep.trusted.effects import grep\n"
        "\n"
        '@clauz3.guarantee(fs.only_read_under("/repo"))\n'
        "def main() -> None:\n"
        '    grep("secret", "/etc/shadow")\n'
    )
    results = _prove(
        tmp_path,
        source,
        import_roots=[FS_ROOT, GREP_ROOT],
        trusted=[FS_TRUSTED, GREP_TRUSTED],
    )
    assert [r.proof.conclusion for r in results] == [Conclusion.OK], (
        "the filesystem policy is silent here"
    )


def test_grep_read_policy_catches_what_filesystem_no_longer_does(
    tmp_path: Path,
) -> None:
    """``grep.only_read_under`` applies the same path rule to grep's effect."""

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
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


def test_filesystem_no_reads_no_longer_reaches_grep(tmp_path: Path) -> None:
    """``fs.no_reads`` is ``read_file`` alone, and grep reads through its own.

    The blunt end of the same narrowing: not a path rule but a flat "reads
    nothing", which a program that greps ``/etc/shadow`` now satisfies.
    """

    source = (
        "import clauz3\n"
        "from tools.filesystem.trusted import contracts as fs\n"
        "from tools.grep.trusted.effects import grep\n"
        "\n"
        "@clauz3.guarantee(fs.no_reads())\n"
        "def main() -> None:\n"
        '    grep("secret", "/etc/shadow")\n'
    )
    results = _prove(
        tmp_path,
        source,
        import_roots=[FS_ROOT, GREP_ROOT],
        trusted=[FS_TRUSTED, GREP_TRUSTED],
    )
    assert [r.proof.conclusion for r in results] == [Conclusion.OK], (
        "the filesystem policy is silent here"
    )


def test_grep_searches_at_most_catches_what_no_reads_no_longer_does(
    tmp_path: Path,
) -> None:
    """The contract to state instead: grep counts its own searches."""

    source = (
        "import clauz3\n"
        "from tools.grep.trusted import contracts as grep_rules\n"
        "from tools.grep.trusted.effects import grep\n"
        "\n"
        "@clauz3.guarantee(grep_rules.searches_at_most(0))\n"
        "def main() -> None:\n"
        '    grep("secret", "/etc/shadow")\n'
    )
    results = _prove(
        tmp_path, source, import_roots=[GREP_ROOT, FS_ROOT], trusted=GREP_TRUSTED
    )
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


@pytest.mark.parametrize(
    ("guarantee", "expected"),
    [
        ("fs.no_reads()", Conclusion.OK),
        ("env.no_env_reads()", Conclusion.FAIL),
        ('fs.only_read_under("/repo")', Conclusion.OK),
        ('env.only_vars(["HOME"])', Conclusion.FAIL),
        ('fs.never_read_under("/repo")', Conclusion.OK),
        ('env.never_vars(["AWS_SECRET_ACCESS_KEY"])', Conclusion.FAIL),
    ],
)
def test_environment_reads_need_the_env_policy(
    tmp_path: Path, guarantee: str, expected: Conclusion
) -> None:
    """Filesystem read policies need an env policy to restrict variable reads."""

    source = (
        "import clauz3\n"
        "from tools.filesystem.trusted import contracts as fs\n"
        "from tools.env.trusted import contracts as env\n"
        "from tools.env.trusted.effects import read_env\n"
        "\n"
        f"@clauz3.guarantee({guarantee})\n"
        "def main() -> None:\n"
        '    read_env("AWS_SECRET_ACCESS_KEY")\n'
    )
    results = _prove(
        tmp_path,
        source,
        import_roots=[FS_ROOT, ENV_ROOT],
        trusted=[FS_TRUSTED, ENV_TRUSTED],
    )
    assert [result.proof.conclusion for result in results] == [expected]
