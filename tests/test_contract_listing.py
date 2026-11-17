"""``clauz3 tools`` and generated skills list contracts by what they resolve to.

A contract helper is registered when its module is imported, however the
decorator is spelled, so ``@clauz3.contract`` and ``from clauz3 import contract
as k`` work in a guarantee. The listings matched the decorator's source text
and omitted them: a contract the agent is not told about is one it cannot
state. Both listings now ask :func:`clauz3.decorators.is_contract`.
"""

import ast
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from clauz3 import decorators
from clauz3.cli import main
from clauz3.libraries.install import install_layer

EFFECTS = """\
import clauz3


@clauz3.trusted
def send(addr: str, *, body: str = "") -> None:
    \"\"\"Send a message.\"\"\"


@clauz3.trusted
def log(level: str, /, *parts: str, sep: str = " ", **fields: str) -> None:
    \"\"\"Write a log line.\"\"\"
"""

CONTRACTS = """\
import clauz3
import clauz3.spec as spec
from clauz3 import contract as k
from clauz3.spec import ContractSpec, contract, effect

Send = effect("send")


@contract
def plain() -> ContractSpec:
    \"\"\"Imported by name.\"\"\"
    return Send.empty()


@clauz3.contract
def qualified() -> ContractSpec:
    \"\"\"Through the package.\"\"\"
    return Send.empty()


@k
def aliased() -> ContractSpec:
    \"\"\"Imported under another name.\"\"\"
    return Send.empty()


@spec.contract(description="At most {count} messages.")
def described(*, count: int) -> ContractSpec:
    \"\"\"Called, through an aliased module.\"\"\"
    return Send.empty()


def helper() -> ContractSpec:
    \"\"\"Not a contract.\"\"\"
    return Send.empty()
"""

#: A decorator that is merely *named* ``contract`` registers nothing.
LOOKALIKE = """\
from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable[..., object])


def contract(func: F) -> F:
    return func


@contract
def lookalike() -> None:
    \"\"\"Not registered with clauz3.\"\"\"
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    trusted = tmp_path / "source/tools/mail/trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text(EFFECTS)
    (trusted / "contracts.py").write_text(CONTRACTS)
    (trusted / "lookalike.py").write_text(LOOKALIKE)
    return tmp_path / "source"


def test_tools_lists_contracts_however_the_decorator_is_spelled(
    project: Path, capsys: CaptureFixture[str]
) -> None:
    assert main(["tools", "--import-root", str(project)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if line.startswith("contract ")] == [
        "contract mail/trusted/contracts.py:plain()",
        "contract mail/trusted/contracts.py:qualified()",
        "contract mail/trusted/contracts.py:aliased()",
        "contract mail/trusted/contracts.py:described(*, count)",
    ]


def test_tools_signature_names_every_parameter(
    project: Path, capsys: CaptureFixture[str]
) -> None:
    """``send(addr)`` hid ``body``, which can only be passed by keyword."""
    assert main(["tools", "--import-root", str(project)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if line.startswith("effect ")] == [
        "effect mail/trusted/effects.py:send(addr, *, body)",
        "effect mail/trusted/effects.py:log(level, /, *parts, sep, **fields)",
    ]


def test_generated_skill_lists_contracts_however_the_decorator_is_spelled(
    project: Path, tmp_path: Path
) -> None:
    install_layer(project, into=tmp_path / "dest", generate_skills=True)

    text = (tmp_path / "dest/.clauz3/skills/mail/SKILL.md").read_text()
    contracts = text[text.index("## Contracts") : text.index("## Usage")]
    assert [line for line in contracts.splitlines() if line.startswith("- ")] == [
        "- `plain() -> ContractSpec` — Imported by name.",
        "- `qualified() -> ContractSpec` — Through the package.",
        "- `aliased() -> ContractSpec` — Imported under another name.",
        "- `described(*, count: int) -> ContractSpec`"
        " — Called, through an aliased module.",
    ]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from clauz3.spec import contract\n@contract\ndef f(): ...", True),
        ("import clauz3\n@clauz3.contract\ndef f(): ...", True),
        ("import clauz3 as c\n@c.contract(description='d')\ndef f(): ...", True),
        ("from clauz3 import contract as k\n@k\ndef f(): ...", True),
        ("import clauz3.spec\n@clauz3.spec.contract\ndef f(): ...", True),
        ("@contract\ndef f(): ...", False),
        ("from json import loads as contract\n@contract\ndef f(): ...", False),
        ("import json as clauz3\n@clauz3.contract\ndef f(): ...", False),
        ("import clauz3\n@clauz3.trusted\ndef f(): ...", False),
    ],
)
def test_is_contract_resolves_the_decorator(source: str, expected: bool) -> None:
    module = ast.parse(source)
    function = module.body[-1]
    assert isinstance(function, ast.FunctionDef)

    aliases = decorators.import_aliases(module)

    assert decorators.is_contract(function, aliases) is expected
