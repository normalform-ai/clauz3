from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from clauz3.cli import main
from clauz3.libraries.install import InstallError, install_layer

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"


def test_install_copies_trusted_layer(tmp_path: Path) -> None:
    result = install_layer(EMAIL_ROOT, into=tmp_path)

    assert result.domains == ["email"]
    copied = tmp_path / ".clauz3/tools/email/trusted/effects.py"
    assert copied.is_file()
    assert "send_email" in copied.read_text()
    assert result.skills == []
    assert len(result.warnings) == 1
    assert "missing tools/email/clauz3.yaml" in result.warnings[0]


def test_install_accepts_tools_dir_directly(tmp_path: Path) -> None:
    result = install_layer(EMAIL_ROOT / "tools", into=tmp_path)

    assert result.domains == ["email"]
    assert (tmp_path / ".clauz3/tools/email/trusted/contracts.py").is_file()


def test_install_generates_skill(tmp_path: Path) -> None:
    result = install_layer(EMAIL_ROOT, into=tmp_path, generate_skills=True)

    skill = tmp_path / ".clauz3/skills/email/SKILL.md"
    assert result.skills == [skill]
    text = skill.read_text()
    assert "@clauz3.guarantee(...)" not in text
    assert "builtin:clauz3-authoring" in text
    assert "name: email" in text
    assert "send_email(addr: str, msg: str) -> None" in text
    assert "only(addresses: list[str])" in text
    assert "trusted email sender." in text


def test_generated_skill_preserves_keyword_only_safety_default(tmp_path: Path) -> None:
    source = tmp_path / "source"
    trusted = source / "tools/email/trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text(
        "import clauz3\n"
        "@clauz3.trusted\n"
        "def send_email(to: str, subject: str, body: str, *, "
        "dry_run: bool = True) -> None:\n"
        '    """Preview by default; send only with dry_run=False."""\n'
    )
    install_layer(source, into=tmp_path / "dest", generate_skills=True)

    text = (tmp_path / "dest/.clauz3/skills/email/SKILL.md").read_text()
    assert (
        "send_email(to: str, subject: str, body: str, *, dry_run: bool=True) -> None"
        in text
    )


def test_install_copies_nested_trusted_layer_and_generates_skill(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    trusted = source / "tools/home/sonos/trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text(
        "\n".join(
            [
                "import deal",
                "",
                '@deal.has("trusted")',
                "def say_sonos(room: str, text: str) -> None:",
                '    """Speak on Sonos."""',
                "    pass",
            ]
        )
    )
    (trusted / "contracts.py").write_text(
        "\n".join(
            [
                "from clauz3.spec import ContractSpec, contract, effect",
                "",
                'Speech = effect("say_sonos")',
                "",
                "@contract",
                "def no_speech() -> ContractSpec:",
                '    """No speech."""',
                "    return Speech.empty()",
            ]
        )
    )
    dest = tmp_path / "dest"

    result = install_layer(source, into=dest, generate_skills=True)

    assert result.domains == ["home/sonos"]
    assert (dest / ".clauz3/tools/home/sonos/trusted/effects.py").is_file()
    skill = dest / ".clauz3/skills/home/sonos/SKILL.md"
    assert result.skills == [skill]
    text = skill.read_text()
    assert "name: home-sonos" in text
    assert "from tools.home.sonos.trusted.effects import <name>" in text
    assert "say_sonos(room: str, text: str) -> None" in text
    assert "no_speech()" in text


def test_install_refuses_existing_without_force(tmp_path: Path) -> None:
    install_layer(EMAIL_ROOT, into=tmp_path)

    with pytest.raises(InstallError, match="already exists"):
        install_layer(EMAIL_ROOT, into=tmp_path)


def test_install_force_overwrites(tmp_path: Path) -> None:
    install_layer(EMAIL_ROOT, into=tmp_path)
    result = install_layer(EMAIL_ROOT, into=tmp_path, force=True)

    assert result.domains == ["email"]


def test_install_missing_source(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="does not exist"):
        install_layer(tmp_path / "nope", into=tmp_path)


def test_install_no_tools_dir(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(InstallError, match="no tools/ directory"):
        install_layer(empty, into=tmp_path)


def test_installed_layer_proves(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    install_layer(EMAIL_ROOT, into=tmp_path)
    case = tmp_path / "plan.py"
    case.write_text(
        "\n".join(
            [
                "import clauz3",
                "from tools.email.trusted import contracts as emails",
                "from tools.email.trusted.effects import send_email",
                "",
                '@clauz3.guarantee(emails.only(["bob@example.com"]))',
                "def main() -> None:",
                '    send_email("bob@example.com", "hi")',
            ]
        )
    )

    result = main(
        [
            "prove",
            str(case),
            "--trusted-root",
            str(tmp_path / ".clauz3/tools/email/trusted"),
            "--import-root",
            str(tmp_path / ".clauz3"),
            "--import-root",
            str(tmp_path),
        ]
    )

    assert result == 0
    assert "main: proved!" in capsys.readouterr().out


def test_install_cli(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    result = main(
        [
            "install",
            str(EMAIL_ROOT),
            "--into",
            str(tmp_path),
            "--skills",
        ]
    )

    assert result == 0
    captured = capsys.readouterr()
    assert "installed trusted layer:" in captured.out
    assert "generated skill:" in captured.out
    assert "warning: email: missing tools/email/clauz3.yaml" in captured.err
    assert (tmp_path / ".clauz3/tools/email/trusted/effects.py").is_file()
    assert (tmp_path / ".clauz3/skills/email/SKILL.md").is_file()


def test_install_without_optional_package_has_no_metadata_warning(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    assert main(["install", "stdlib:env", "--into", str(tmp_path)]) == 0
    assert not capsys.readouterr().err


def test_generated_skill_lists_an_aliased_effect(tmp_path: Path) -> None:
    """The listing resolves decorators, so it cannot disagree with the prover.

    An effect the skill omits is one the agent does not know it may call.
    Matching the decorator's source text left a layer written as ``import
    clauz3 as c`` listed incompletely while proving correctly.
    """

    source = tmp_path / "source"
    trusted = source / "tools/home/trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text(
        "import clauz3 as c\n"
        "import deal as d\n"
        "\n"
        "\n"
        "@c.trusted\n"
        "def lock_door(room: str) -> None:\n"
        '    """Lock a door."""\n'
        "\n"
        "\n"
        '@d.has("io")\n'
        "def open_blind(room: str) -> None:\n"
        '    """Open a blind."""\n'
        "\n"
        "\n"
        "def helper(room: str) -> str:\n"
        '    """Not an effect."""\n'
        "    return room\n"
    )
    (trusted / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract\n"
        "from clauz3.spec import no_guarantees as core\n"
        "\n"
        "\n"
        "@contract\n"
        "def anything() -> ContractSpec:\n"
        '    """Impose nothing."""\n'
        "    return core()\n"
    )

    install_layer(source, into=tmp_path / "dest", generate_skills=True)

    text = (tmp_path / "dest/.clauz3/skills/home/SKILL.md").read_text()
    assert "lock_door(room: str) -> None" in text
    assert "open_blind(room: str) -> None" in text
    assert "helper(room:" not in text
    assert "anything() -> ContractSpec" in text
