"""``clauz3 install`` replaces a domain cleanly and changes nothing on refusal.

The installed tree is what the prover trusts, so it has to be exactly the
source tree plus the files install generates. Merging a newer source into the
old copy kept modules that were deleted upstream installed and trusted, and
checking each destination only as its turn came left earlier domains (and
dependencies) installed when a later one was refused.
"""

from pathlib import Path

import pytest

from clauz3.libraries.install import InstallError, install_layer, install_library

EFFECT = "import clauz3\n\n\n@clauz3.trusted\ndef {name}(x: str) -> None:\n    pass\n"
GENERATED = {".clauz3-origin.json", ".clauz3-package.yaml", ".gitignore"}


def _domain(
    root: Path,
    domain: str,
    files: dict[str, str],
    *,
    dependencies: tuple[str, ...] = (),
) -> Path:
    directory = root / "tools" / domain
    (directory / "trusted").mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (directory / "trusted" / name).write_text(text)
    listed = "".join(f"\n  - {dependency}" for dependency in dependencies)
    (directory / "clauz3.yaml").write_text(
        f"schema_version: 1\nname: {domain}\ntitle: T\nsummary: s\ndescription: d\n"
        + (f"dependencies:{listed}\n" if dependencies else "")
    )
    return root


def _tree(root: Path) -> list[str]:
    """Every authored file below ``root``, without the ones install generates."""
    if not root.exists():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in GENERATED
    )


def test_force_removes_a_trusted_module_deleted_upstream(tmp_path: Path) -> None:
    source = _domain(
        tmp_path / "source",
        "lib",
        {
            "effects.py": EFFECT.format(name="keep"),
            "old.py": EFFECT.format(name="danger"),
        },
    )
    project = tmp_path / "project"
    install_library(str(source), into=project)
    assert (project / ".clauz3/tools/lib/trusted/old.py").is_file()

    (source / "tools/lib/trusted/old.py").unlink()
    install_library(str(source), into=project, force=True)

    assert _tree(project / ".clauz3/tools") == _tree(source / "tools")
    assert (project / ".clauz3/tools/lib/.clauz3-origin.json").is_file()
    assert not list((project / ".clauz3").glob("install-*"))


def test_force_drops_a_manifest_and_package_removed_upstream(tmp_path: Path) -> None:
    source = _domain(
        tmp_path / "source", "lib", {"effects.py": EFFECT.format(name="a")}
    )
    (source / "clauz3.yaml").write_text(
        "schema_version: 1\nkind: package\nname: bundle\ntitle: Bundle\n"
    )
    project = tmp_path / "project"
    install_layer(source, into=project)
    installed = project / ".clauz3/tools/lib"
    assert (installed / ".clauz3-package.yaml").is_file()

    (source / "clauz3.yaml").unlink()
    (source / "tools/lib/clauz3.yaml").unlink()
    install_layer(source, into=project, force=True)

    assert sorted(path.name for path in installed.iterdir()) == [
        ".clauz3-origin.json",
        "trusted",
    ]


def test_force_replaces_a_user_wide_copy_cleanly(tmp_path: Path) -> None:
    source = _domain(
        tmp_path / "source",
        "lib",
        {
            "effects.py": EFFECT.format(name="keep"),
            "old.py": EFFECT.format(name="danger"),
        },
    )
    store = tmp_path / "store"
    install_layer(source, into=store, globally=True)
    (source / "tools/lib/trusted/old.py").unlink()

    install_layer(source, into=store, globally=True, force=True)

    assert _tree(store / "tools") == _tree(source / "tools")
    assert not list(store.glob("install-*"))


def test_force_refuses_to_delete_a_library_installed_inside_the_domain(
    tmp_path: Path,
) -> None:
    outer = _domain(tmp_path / "outer", "acme", {"effects.py": EFFECT.format(name="a")})
    inner = _domain(
        tmp_path / "inner", "acme/lims", {"effects.py": EFFECT.format(name="b")}
    )
    project = tmp_path / "project"
    install_layer(outer, into=project)
    install_layer(inner, into=project)

    with pytest.raises(InstallError, match="separately installed"):
        install_layer(outer, into=project, force=True)

    assert (project / ".clauz3/tools/acme/lims/trusted/effects.py").is_file()
    assert (project / ".clauz3/tools/acme/trusted/effects.py").is_file()
