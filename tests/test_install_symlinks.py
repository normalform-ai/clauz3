"""Issue #73: install_layer must not follow symlinks out of the source tree.

``copytree`` with the default ``symlinks=False`` dereferences symlinks, so a
malicious layer containing ``tools/<domain>/trusted/x -> /victim/.ssh/id_ed25519``
would materialize that file's content into the destination project. The same
symlink swapped for a ``.py`` becomes code execution at prove time, since
``_load_trusted_modules`` imports every ``.py`` under each trusted root.

The installer must reject symlinks in the source layer rather than copy
through them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clauz3.libraries.install import InstallError, install_layer


def _make_layer(root: Path) -> Path:
    trusted = root / "tools/home/trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text(
        'import deal\n\n@deal.has("trusted")\n'
        "def act() -> None:\n"
        '    """Act."""\n'
        "    pass\n"
    )
    (trusted / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        'Act = effect("act")\n\n'
        "@contract\n"
        "def no_acts() -> ContractSpec:\n"
        '    """No acts."""\n'
        "    return Act.empty()\n"
    )
    return root


def test_install_rejects_symlinked_file_in_layer(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET KEY\n")

    source = _make_layer(tmp_path / "source")
    link = source / "tools/home/trusted/stolen.py"
    link.symlink_to(secret)

    dest = tmp_path / "dest"
    with pytest.raises(InstallError):
        install_layer(source, into=dest)

    # The secret content must not have been copied into the destination.
    assert not (dest / ".clauz3/tools/home/trusted/stolen.py").exists()


def test_install_rejects_symlinked_dir_in_layer(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.py").write_text("print('pwned')\n")

    source = _make_layer(tmp_path / "source")
    (source / "tools/home/trusted/sub").symlink_to(outside, target_is_directory=True)

    dest = tmp_path / "dest"
    with pytest.raises(InstallError):
        install_layer(source, into=dest)


def test_install_plain_layer_still_works(tmp_path: Path) -> None:
    source = _make_layer(tmp_path / "source")
    dest = tmp_path / "dest"

    result = install_layer(source, into=dest)

    assert result.domains == ["home"]
    assert (dest / ".clauz3/tools/home/trusted/effects.py").is_file()
