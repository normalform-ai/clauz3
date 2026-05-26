from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from clauz3.cli import main
from clauz3.install import InstallError, install_layer

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"


def test_install_copies_trusted_layer(tmp_path: Path) -> None:
    result = install_layer(EMAIL_ROOT, into=tmp_path)

    assert result.domains == ["email"]
    copied = tmp_path / "tools/email/trusted/effects.py"
    assert copied.is_file()
    assert "send_email" in copied.read_text()
    assert result.skills == []


def test_install_accepts_tools_dir_directly(tmp_path: Path) -> None:
    result = install_layer(EMAIL_ROOT / "tools", into=tmp_path)

    assert result.domains == ["email"]
    assert (tmp_path / "tools/email/trusted/contracts.py").is_file()


def test_install_generates_skill(tmp_path: Path) -> None:
    result = install_layer(EMAIL_ROOT, into=tmp_path, generate_skills=True)

    skill = tmp_path / "agents/skills/email/SKILL.md"
    assert result.skills == [skill]
    text = skill.read_text()
    assert "name: email" in text
    assert "send_email(addr: str, msg: str) -> None" in text
    assert "only(addresses: list[str])" in text
    assert "trusted email sender." in text


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
            str(tmp_path / "tools/email/trusted"),
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
    out = capsys.readouterr().out
    assert "installed trusted layer:" in out
    assert "generated skill:" in out
    assert (tmp_path / "tools/email/trusted/effects.py").is_file()
    assert (tmp_path / "agents/skills/email/SKILL.md").is_file()
