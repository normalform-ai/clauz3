"""Every bundled stdlib guarantee shows a plain-language sentence on approval.

Without a ``@contract(description=...)`` template the approval card falls back
to the docstring's first line, which is written for developers. This guard
loads each bundled library's trusted contracts, requires a template on every
contract (apart from the named exemptions below), and renders each template
with sample arguments so a broken template fails here rather than at approval.
"""

import importlib
import inspect
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from clauz3 import DirectoryPath, FilePath
from clauz3.contract_metadata import (
    DESCRIPTION_ATTRIBUTE,
    describe_call,
    element_kind,
    format_literal,
    parameter_types,
)
from clauz3.spec import REGISTERED_CONTRACTS

STDLIB = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib"
CONTRACT_FILES = sorted(STDLIB.glob("*/cz3tools/**/trusted/contracts.py"))

#: Contracts whose parameter types the description renderer cannot display.
EXEMPT = {
    "cz3tools.db.trusted.contracts.only_where": (
        "filter_dict is a dict, which description templates cannot render"
    ),
}

#: Developer words an ordinary person approving a run should not need to know.
JARGON = re.compile(
    r"\b(verbs?|subcommands?|invocations?|allowlists?|blocklists?|effects?|"
    r"relations?|predicates?)\b",
    re.IGNORECASE,
)

SAMPLES: dict[str, object] = {
    "str": "sample",
    "int": 3,
    "float": 1.5,
    "bool": True,
    "FilePath": FilePath("data/sample.txt"),
    "DirectoryPath": DirectoryPath("data"),
}


def _forget_layers() -> None:
    for name in [
        n
        for n in sys.modules
        if n in ("cz3tools", "tools") or n.startswith(("cz3tools.", "tools."))
    ]:
        del sys.modules[name]


def _load(path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import a library's contracts module from its own source root.

    Every library (and examples such as gh-triage) uses the ``cz3tools`` package
    name, so previously imported layers are forgotten first and the root is
    removed again afterwards; the module object stays usable.
    """
    root = next(parent for parent in path.parents if parent.name == "cz3tools").parent
    relative = path.relative_to(root).with_suffix("")
    _forget_layers()
    with monkeypatch.context() as scoped:
        scoped.syspath_prepend(str(root))
        module = importlib.import_module(".".join(relative.parts))
    _forget_layers()
    return module


def _contracts(module: ModuleType) -> dict[str, Callable[..., object]]:
    return {
        f"{module.__name__}.{name}": value
        for name, value in vars(module).items()
        if callable(value)
        and getattr(value, "__module__", None) == module.__name__
        and f"{module.__name__}.{name}" in REGISTERED_CONTRACTS
    }


def _sample(kind: str) -> object:
    element = element_kind(kind)
    return SAMPLES[kind] if element is None else [SAMPLES[element]]


def test_every_stdlib_library_is_checked() -> None:
    libraries = {path.relative_to(STDLIB).parts[0] for path in CONTRACT_FILES}
    bundled = {path.name for path in STDLIB.iterdir() if path.is_dir()}
    assert libraries == bundled


@pytest.mark.parametrize(
    "path", CONTRACT_FILES, ids=[str(p.relative_to(STDLIB)) for p in CONTRACT_FILES]
)
def test_stdlib_contracts_have_rendered_plain_descriptions(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contracts = _contracts(_load(path, monkeypatch))
    assert contracts, f"no contracts registered from {path}"
    for symbol, helper in contracts.items():
        template = getattr(helper, DESCRIPTION_ATTRIBUTE, None)
        if symbol in EXEMPT:
            assert template is None, f"{symbol} now has a description; unexempt it"
            with pytest.raises(ValueError, match="unsupported description parameter"):
                parameter_types(inspect.signature(helper))
            continue
        assert isinstance(template, str), f"{symbol} has no description template"
        kinds = parameter_types(inspect.signature(helper))
        args = {name: _sample(kind) for name, kind in kinds.items()}
        described = describe_call(helper, args=[], kwargs=args)
        assert described is not None, f"{symbol} description does not render"
        text = str(described["text"])
        for name, kind in kinds.items():
            assert format_literal(args[name], kind) in text
        assert "{" not in text, f"{symbol} left a placeholder: {text}"
        assert not JARGON.search(text), f"{symbol} description uses jargon: {text}"


def test_exemptions_name_existing_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    found: set[str] = set()
    for path in CONTRACT_FILES:
        found.update(_contracts(_load(path, monkeypatch)))
    assert set(EXEMPT) <= found


@pytest.mark.parametrize(
    "library,constant",
    [("gh", "READ_ONLY_ACTIONS"), ("git", "READ_ONLY_SUBCOMMANDS")],
)
def test_read_only_descriptions_name_every_permitted_command(
    library: str, constant: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = STDLIB / library / "cz3tools" / library / "trusted/contracts.py"
    module = _load(path, monkeypatch)
    template = getattr(module.read_only, DESCRIPTION_ATTRIBUTE)
    words = set(re.findall(r"[a-z][a-z-]*", template))
    assert set(getattr(module, constant)) <= words
