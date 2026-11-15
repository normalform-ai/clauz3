"""Backwards-compatibility tests for ``cz3tools`` and legacy ``tools`` layouts."""

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from clauz3 import interp
from clauz3._vendor import deal_solver
from clauz3.cli import main
from clauz3.guarantees import (
    contract_documentation,
    guarantee_texts,
)
from clauz3.imports_policy import build_policy
from clauz3.libraries.discovery import installed_domains
from clauz3.libraries.install import InstallError, install_layer
from clauz3.libraries.layout import (
    LayoutError,
    check_project,
    domain_name,
    domain_path,
    expand_import_roots,
    tools_dirs,
)
from clauz3.policy import compute_coverage, load_domain_policies
from clauz3.prover import ProverConfigError, prove_path, prove_text
from clauz3.runner import discover_trusted_roots
from clauz3.sessions.catalogue import snapshot_catalogue

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
EMAIL_TRUSTED = EMAIL_ROOT / "cz3tools/email/trusted"


def _write_legacy_layer(base: Path, domain: str = "demo") -> Path:
    pkg = base.name
    trusted = base / domain / "trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text(
        "import clauz3\n\n"
        "@clauz3.trusted\n"
        "def ping(host: str) -> None:\n"
        '    """Ping a host."""\n'
        "    pass\n"
    )
    (trusted / "contracts.py").write_text(
        f"from {pkg}.{domain}.trusted.effects import ping\n"
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        "Ping = effect(ping)\n\n"
        "@contract\n"
        "def only_host(allowed: str) -> ContractSpec:\n"
        '    """Allow only one host."""\n'
        "    return Ping.all(lambda call: call.host == allowed)\n"
    )
    (trusted / "policy.py").write_text(
        "from clauz3.policy import domain_policy\n\n"
        "POLICY = domain_policy(\n"
        '    when_used="ping",\n'
        '    recommended=("only_host",),\n'
        ")\n"
    )
    return trusted


def test_cz3tools_layer_accepts_legacy_tools_imports(tmp_path: Path) -> None:
    """Entry importing ``tools.<domain>.trusted`` resolves a ``cz3tools/`` layer."""
    passing = tmp_path / "pass_case.py"
    passing.write_text(
        "import clauz3\n"
        "from tools.email.trusted import contracts as emails\n"
        "from tools.email.trusted.effects import send_email\n\n"
        '@clauz3.guarantee(emails.only(["bob@example.com"]))\n'
        "def main() -> None:\n"
        '    send_email("bob@example.com", "hi")\n'
    )
    results = prove_path(
        passing,
        trusted_roots=[EMAIL_TRUSTED],
        import_roots=[EMAIL_ROOT],
    )
    assert [r.proof.conclusion for r in results] == [deal_solver.Conclusion.OK]

    policy = build_policy(trusted_roots=[EMAIL_TRUSTED], import_roots=[EMAIL_ROOT])
    outcome = interp.execute(
        passing.read_text(),
        source_name=str(passing),
        policy=policy,
        import_roots=[EMAIL_ROOT],
    )
    assert outcome.effects == (
        ("tools.email.trusted.effects.send_email", ("bob@example.com", "hi"), {}),
    )

    failing = tmp_path / "fail_case.py"
    failing.write_text(
        "import clauz3\n"
        "from tools.email.trusted import contracts as emails\n"
        "from tools.email.trusted.effects import send_email\n\n"
        '@clauz3.guarantee(emails.only(["bob@example.com"]))\n'
        "def main() -> None:\n"
        '    send_email("alice@example.com", "hi")\n'
    )
    fail_results = prove_path(
        failing,
        trusted_roots=[EMAIL_TRUSTED],
        import_roots=[EMAIL_ROOT],
    )
    assert [r.proof.conclusion for r in fail_results] == [deal_solver.Conclusion.FAIL]


@pytest.mark.parametrize("layout_dir", ["tools", ".clauz3/tools"])
@pytest.mark.parametrize("import_pkg", ["tools", "cz3tools"])
def test_legacy_tools_directory_supports_both_import_prefixes(
    tmp_path: Path, layout_dir: str, import_pkg: str
) -> None:
    """Legacy ``tools/`` and ``.clauz3/tools/`` work with either import prefix."""
    trusted = _write_legacy_layer(tmp_path / layout_dir)
    import_roots = expand_import_roots([tmp_path])
    discovered = discover_trusted_roots(import_roots=import_roots)
    assert [p.resolve() for p in discovered] == [trusted.resolve()]
    assert installed_domains(tmp_path) == {"demo"}
    assert domain_path(tmp_path, "demo") == tmp_path / layout_dir / "demo"
    assert domain_name(tmp_path, trusted) == "demo"
    assert tools_dirs(tmp_path) == [tmp_path / layout_dir]

    api = "\n".join(snapshot_catalogue(tmp_path, discovered)["api"])
    assert "from cz3tools.demo.trusted.effects import ping" in api

    source = (
        "import clauz3\n"
        f"from {import_pkg}.demo.trusted import contracts as rules\n"
        f"from {import_pkg}.demo.trusted.effects import ping\n\n"
        '@clauz3.guarantee(rules.only_host("example.com"))\n'
        "def main() -> None:\n"
        '    ping("example.com")\n'
    )
    entry = tmp_path / "entry.py"
    entry.write_text(source)
    results = prove_path(
        entry,
        trusted_roots=discovered,
        import_roots=import_roots,
    )
    assert [r.proof.conclusion for r in results] == [deal_solver.Conclusion.OK]

    policy = build_policy(trusted_roots=discovered, import_roots=import_roots)
    outcome = interp.execute(
        source,
        source_name=str(entry),
        policy=policy,
        import_roots=import_roots,
    )
    assert outcome.effects == (
        (f"{import_pkg}.demo.trusted.effects.ping", ("example.com",), {}),
    )

    docs = contract_documentation(source=source, target="main")
    assert docs == {"rules.only_host('example.com')": "Allow only one host."}

    policies = load_domain_policies(trusted_roots=discovered, import_roots=import_roots)
    coverage = compute_coverage(
        source=source,
        guarantees=guarantee_texts(source=source, target="main"),
        policies=policies,
        import_roots=import_roots,
        trusted_roots=discovered,
    )
    assert [item["status"] for item in coverage] == ["covered"]


@pytest.mark.parametrize("import_pkg", ["cz3tools", "tools"])
def test_install_from_legacy_tools_source_rewrites_internal_imports(
    tmp_path: Path, import_pkg: str
) -> None:
    """Installing a legacy ``tools/`` library into ``.clauz3/cz3tools/`` proves."""
    source_repo = tmp_path / "legacy_repo"
    _write_legacy_layer(source_repo / "tools", domain="demo")

    project = tmp_path / "project"
    project.mkdir()
    installed = install_layer(source_repo, into=project)
    assert installed.domains == ["demo"]
    assert (project / ".clauz3/cz3tools/demo/trusted/contracts.py").is_file()
    assert any("missing tools/demo/clauz3.yaml" in w for w in installed.warnings)

    case = project / "plan.py"
    case.write_text(
        "import clauz3\n"
        f"from {import_pkg}.demo.trusted import contracts as rules\n"
        f"from {import_pkg}.demo.trusted.effects import ping\n\n"
        '@clauz3.guarantee(rules.only_host("example.com"))\n'
        "def main() -> None:\n"
        '    ping("example.com")\n'
    )
    import_roots = expand_import_roots([project])
    trusted_roots = discover_trusted_roots(import_roots=import_roots)
    results = prove_path(
        case,
        trusted_roots=trusted_roots,
        import_roots=import_roots,
    )
    assert [r.proof.conclusion for r in results] == [deal_solver.Conclusion.OK]


def test_check_project_detects_collision_between_cz3tools_and_legacy_tools(
    tmp_path: Path,
) -> None:
    """Duplicate domains across ``cz3tools/`` and ``tools/`` are refused."""
    (tmp_path / "cz3tools/demo/trusted").mkdir(parents=True)
    (tmp_path / "tools/demo/trusted").mkdir(parents=True)
    with pytest.raises(LayoutError, match="demo provided by both"):
        check_project(tmp_path)


def test_pypi_tools_package_on_sys_path_does_not_shadow_cz3tools(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A regular ``tools`` package in site-packages cannot shadow ``cz3tools``."""
    site_pkgs = tmp_path / "site_packages"
    pypi_tools = site_pkgs / "tools"
    pypi_tools.mkdir(parents=True)
    (pypi_tools / "__init__.py").write_text(" VERSION = '1.0'\n")
    monkeypatch.syspath_prepend(str(site_pkgs))

    project = tmp_path / "project"
    install_layer("stdlib:filesystem", into=project)
    case = project / "plan.py"
    case.write_text(
        "import clauz3\n"
        "from cz3tools.filesystem.trusted import contracts as fs\n"
        "from cz3tools.filesystem.trusted.effects import read_file\n\n"
        '@clauz3.guarantee(fs.only_reads_files([clauz3.FilePath("notes.txt")]))\n'
        "@clauz3.guarantee(fs.read_only())\n"
        "def main() -> None:\n"
        '    read_file("notes.txt")\n'
    )
    monkeypatch.chdir(project)
    assert (
        main(
            [
                "prove",
                "--trusted-root",
                str(project / ".clauz3/cz3tools/filesystem/trusted"),
                str(case),
            ]
        )
        == 0
    )


@pytest.mark.parametrize(
    ("trusted_pkg", "shadow_pkg"),
    [("cz3tools", "tools"), ("tools", "cz3tools")],
)
@pytest.mark.parametrize("import_pkg", ["cz3tools", "tools"])
def test_untrusted_alternate_tools_dir_cannot_shadow_trusted_layer(
    tmp_path: Path,
    trusted_pkg: str,
    shadow_pkg: str,
    import_pkg: str,
) -> None:
    """An untrusted alternate tools directory never shadows or aliases."""
    project = tmp_path / "project"
    trusted = _write_legacy_layer(project / trusted_pkg, domain="demo")
    cases = project / "cases"
    cases.mkdir(parents=True)
    marker = tmp_path / "ran_untrusted"

    shadow = cases / shadow_pkg / "demo" / "trusted"
    shadow.mkdir(parents=True)
    (shadow / "effects.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('pwned')\n"
        "def ping(host: str) -> None:\n    pass\n"
    )
    (shadow / "contracts.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('pwned')\n"
    )

    source = (
        "import clauz3\n"
        f"from {import_pkg}.demo.trusted import contracts as rules\n"
        f"from {import_pkg}.demo.trusted.effects import ping\n\n"
        '@clauz3.guarantee(rules.only_host("example.com"))\n'
        "def main() -> None:\n"
        '    ping("example.com")\n'
    )
    entry = cases / "entry.py"
    entry.write_text(source)
    import_roots = [project, cases]

    with pytest.raises(ProverConfigError):
        prove_path(entry, trusted_roots=[trusted], import_roots=import_roots)

    with pytest.raises(ProverConfigError):
        prove_text(
            source,
            source_name=str(entry),
            trusted_roots=[trusted],
            import_paths=import_roots,
        )

    policy = build_policy(trusted_roots=[trusted], import_roots=import_roots)
    with pytest.raises(interp.InterpError):
        interp.execute(
            source,
            source_name=str(entry),
            policy=policy,
            import_roots=import_roots,
        )

    assert not marker.exists()


def test_untrusted_tools_init_blocks_legacy_alias_without_breaking_cz3tools(
    tmp_path: Path,
) -> None:
    """An untrusted ``tools/__init__.py`` blocks ``tools.*`` aliasing cleanly."""
    project = tmp_path / "project"
    trusted = _write_legacy_layer(project / "cz3tools", domain="demo")
    cases = project / "cases"
    untrusted_tools = cases / "tools"
    untrusted_tools.mkdir(parents=True)
    marker = tmp_path / "init_ran"
    (untrusted_tools / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
    )
    import_roots = [project, cases]

    legacy_source = (
        "import clauz3\n"
        "from tools.demo.trusted import contracts as rules\n"
        "from tools.demo.trusted.effects import ping\n\n"
        '@clauz3.guarantee(rules.only_host("example.com"))\n'
        "def main() -> None:\n"
        '    ping("example.com")\n'
    )
    legacy_entry = cases / "legacy.py"
    legacy_entry.write_text(legacy_source)

    with pytest.raises(ProverConfigError):
        prove_path(legacy_entry, trusted_roots=[trusted], import_roots=import_roots)

    policy = build_policy(trusted_roots=[trusted], import_roots=import_roots)
    with pytest.raises(interp.InterpError):
        interp.execute(
            legacy_source,
            source_name=str(legacy_entry),
            policy=policy,
            import_roots=import_roots,
        )
    assert not marker.exists()

    primary_source = (
        "import clauz3\n"
        "from cz3tools.demo.trusted import contracts as rules\n"
        "from cz3tools.demo.trusted.effects import ping\n\n"
        '@clauz3.guarantee(rules.only_host("example.com"))\n'
        "def main() -> None:\n"
        '    ping("example.com")\n'
    )
    primary_entry = cases / "primary.py"
    primary_entry.write_text(primary_source)
    results = prove_path(
        primary_entry, trusted_roots=[trusted], import_roots=import_roots
    )
    assert [r.proof.conclusion for r in results] == [deal_solver.Conclusion.OK]
    assert not marker.exists()


def test_ambiguous_dual_domains_fail_closed_in_discovery_install_and_prove(
    tmp_path: Path,
) -> None:
    """Dual ``cz3tools/<d>/trusted`` and ``tools/<d>/trusted`` fail closed."""
    cz3_trusted = _write_legacy_layer(tmp_path / "cz3tools", domain="demo")
    legacy_trusted = _write_legacy_layer(tmp_path / "tools", domain="demo")

    with pytest.raises(LayoutError, match="demo provided by both"):
        check_project(tmp_path)

    with pytest.raises(LayoutError, match="demo provided by both"):
        discover_trusted_roots(import_roots=[tmp_path])

    with pytest.raises(InstallError, match="contains both cz3tools/ and tools/"):
        install_layer(tmp_path, into=tmp_path / "dest")

    entry = tmp_path / "entry.py"
    entry.write_text(
        "import clauz3\n"
        "from cz3tools.demo.trusted import contracts as rules\n"
        "from cz3tools.demo.trusted.effects import ping\n\n"
        '@clauz3.guarantee(rules.only_host("example.com"))\n'
        "def main() -> None:\n"
        '    ping("example.com")\n'
    )
    with pytest.raises(ProverConfigError):
        prove_path(
            entry,
            trusted_roots=[cz3_trusted, legacy_trusted],
            import_roots=[tmp_path],
        )


def test_symlinked_tools_directory_cannot_alias_or_escape_trusted_roots(
    tmp_path: Path,
) -> None:
    """A symlinked ``tools`` directory outside trusted roots blocks aliasing."""
    project = tmp_path / "project"
    trusted = _write_legacy_layer(project / "cz3tools", domain="demo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "tools").symlink_to(outside, target_is_directory=True)

    entry = project / "entry.py"
    entry.write_text(
        "import clauz3\n"
        "from tools.demo.trusted import contracts as rules\n"
        "from tools.demo.trusted.effects import ping\n\n"
        '@clauz3.guarantee(rules.only_host("example.com"))\n'
        "def main() -> None:\n"
        '    ping("example.com")\n'
    )
    with pytest.raises(ProverConfigError):
        prove_path(entry, trusted_roots=[trusted], import_roots=[project])
