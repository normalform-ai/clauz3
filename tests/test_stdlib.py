from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.cli import main
from clauz3.libraries.discovery import Library
from clauz3.libraries.install import (
    InstallError,
    available_stdlib_tools,
    install_layer,
    install_library,
)
from clauz3.prover import ProofResult, prove_path

ROOT = Path(__file__).resolve().parents[1]
STDLIB = ROOT / "src/clauz3/stdlib"
FS_ROOT = STDLIB / "filesystem"
FS_TRUSTED = FS_ROOT / "tools/filesystem/trusted"
GIT_ROOT = STDLIB / "git"
GIT_TRUSTED = GIT_ROOT / "tools/git/trusted"
GH_ROOT = STDLIB / "gh"
GH_TRUSTED = GH_ROOT / "tools/gh/trusted"
RSYNC_ROOT = STDLIB / "rsync"
RSYNC_TRUSTED = RSYNC_ROOT / "tools/rsync/trusted"
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


def test_available_stdlib_tools_lists_bundled_tools() -> None:
    tools = available_stdlib_tools()
    assert "db" in tools
    assert "filesystem" in tools
    assert "git" in tools
    assert "gh" in tools
    assert "rsync" in tools


def test_install_stdlib_scheme(tmp_path: Path) -> None:
    result = install_layer("stdlib:filesystem", into=tmp_path)

    assert result.domains == ["filesystem"]
    copied = tmp_path / ".clauz3/tools/filesystem/trusted/effects.py"
    assert copied.is_file()
    assert "read_file" in copied.read_text()


def test_install_stdlib_bare_name(tmp_path: Path) -> None:
    result = install_layer("git", into=tmp_path)

    assert result.domains == ["git"]
    assert (tmp_path / ".clauz3/tools/git/trusted/contracts.py").is_file()


def test_install_unknown_stdlib_tool_lists_available(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="stdlib:filesystem"):
        install_layer("stdlib:nope", into=tmp_path)


def test_install_stdlib_cli(tmp_path: Path) -> None:
    assert main(["install", "stdlib:filesystem", "--into", str(tmp_path)]) == 0
    assert (tmp_path / ".clauz3/tools/filesystem/trusted/effects.py").is_file()


# ── one filesystem policy reaches every tool that performs the event ────────
#
# Each effect declares the filesystem events it performs, and the contracts are
# stated over events, so a policy covers a tool it never names. These were
# separate per-tool contracts (and silent bypasses) when grep, find, text,
# editor and coreutils were libraries of their own.


@pytest.mark.parametrize(
    ("guarantee", "call"),
    [
        ('fs.only_read_under("/repo")', 'grep("secret", "/etc/shadow")'),
        ('fs.only_read_under("/repo")', 'find("/etc", ["-name", "*.conf"])'),
        ('fs.only_read_under("/repo")', 'read_line("/etc/shadow", 1)'),
        (
            'fs.never_read_under("/home/user/.ssh")',
            'copy("/home/user/.ssh/id_rsa", "/tmp/k")',
        ),
        (
            'fs.never_read_under("/repo/PRECIOUS.md")',
            'move("/repo/PRECIOUS.md", "/tmp/x")',
        ),
        ("fs.no_reads()", 'grep("secret", "/etc/shadow")'),
        ('fs.only_write_under("/repo/build")', 'make_dir("/etc/cron.d")'),
        ('fs.only_write_under("/repo/build")', 'copy("/repo/a", "/etc/cron.d/job")'),
        ('fs.only_write_under("/repo/build")', 'remove("/etc/passwd")'),
        ('fs.only_write_under("/repo/build")', 'append_file("/etc/hosts", "x")'),
        ("fs.read_only()", 'chmod("/repo/run.sh", 0o755)'),
        ('fs.never_delete_under("/home/user/.ssh")', 'remove("/home/user")'),
    ],
)
def test_filesystem_policy_reaches_every_tool(
    tmp_path: Path, guarantee: str, call: str
) -> None:
    source = (
        "import clauz3\n"
        "from tools.filesystem.trusted import contracts as fs\n"
        "from tools.filesystem.trusted.effects import (\n"
        "    append_file, chmod, copy, find, grep, make_dir, move, read_line, remove\n"
        ")\n"
        "\n"
        f"@clauz3.guarantee({guarantee})\n"
        "def main() -> None:\n"
        f"    {call}\n"
    )
    results = _prove(tmp_path, source, import_roots=[FS_ROOT], trusted=FS_TRUSTED)
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


FS_CASES = sorted((FS_ROOT / "tests/cases").glob("*.py"))


@pytest.mark.parametrize("case", FS_CASES, ids=[case.stem for case in FS_CASES])
def test_filesystem_case_conclusion_is_exact(case: Path) -> None:
    """A ``_fail`` case must fail its proof, not merely stop on a refusal.

    The case harness accepts a configuration error as a failure, so a typo in
    a case would still pass there; this pins the conclusion itself.
    """

    expected = Conclusion.OK if case.stem.endswith("_pass") else Conclusion.FAIL
    results = prove_path(case, trusted_roots=[FS_TRUSTED], import_roots=[FS_ROOT])
    assert [r.proof.conclusion for r in results] == [expected]


@pytest.mark.parametrize("prefix", ["data//secret", "data/./secret", "data/../x"])
def test_a_prefix_no_effect_path_can_match_is_refused(
    tmp_path: Path, prefix: str
) -> None:
    """A deny-list no path can be written as would hold vacuously."""

    source = (
        "import clauz3\n"
        "from tools.filesystem.trusted import contracts as fs\n"
        "from tools.filesystem.trusted.effects import read_file\n"
        "\n"
        f"@clauz3.guarantee(fs.never_read_under({prefix!r}))\n"
        "def main() -> None:\n"
        '    print(read_file("data/secret"))\n'
    )
    results = _prove(tmp_path, source, import_roots=[FS_ROOT], trusted=FS_TRUSTED)
    # The guarantee is refused; the program's other obligations still prove.
    assert [r.proof.conclusion for r in results] == [Conclusion.PARTIAL]
    assert "is not written the way effect paths are" in str(results[0].proof.error)


@pytest.mark.parametrize(
    ("guarantee", "path"),
    [
        ('fs.never_read_under("/home/user/.ssh")', "/home/user//.ssh/id_rsa"),
        ('fs.never_read_under("/home/user/.ssh")', "/home/user/./.ssh/id_rsa"),
        ('fs.never_read_under("/home/user/.ssh")', "/tmp/../home/user/.ssh/id_rsa"),
        ('fs.only_read_under("/repo/")', "/repo/../etc/passwd"),
    ],
)
def test_a_respelled_path_from_another_layers_tool_is_refused(
    tmp_path: Path, dependent_library: Library, guarantee: str, path: str
) -> None:
    """The contract, not only the filesystem tools, refuses a respelled path.

    ``search`` performs ``Read`` without the filesystem effects' path
    precondition, so the event path reaches the contract as written.
    """

    install_library(dependent_library.source, into=tmp_path)
    tools = tmp_path / ".clauz3/tools"
    source = (
        "import clauz3\n"
        "from tools.filesystem.trusted import contracts as fs\n"
        "from tools.search.trusted.effects import search\n"
        "\n"
        f"@clauz3.guarantee({guarantee})\n"
        "def main() -> None:\n"
        f"    print(search('BEGIN', {path!r}))\n"
    )
    results = _prove(
        tmp_path,
        source,
        import_roots=[tmp_path / ".clauz3"],
        trusted=[tools / "filesystem/trusted", tools / "search/trusted"],
    )
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]
