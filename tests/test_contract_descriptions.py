"""Trusted CNL is typed, literal, stable, and independent of function execution."""

import inspect
import json
import shutil
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.approvals.descriptions import render_guarantees
from clauz3.contract_metadata import (
    DESCRIPTION_ATTRIBUTE,
    describe_call,
    parameter_types,
    validate_template,
)
from clauz3.guarantees import contract_descriptions
from clauz3.libraries.contract_schema import bundled_schema, schema_for_sources
from clauz3.prover import (
    ProverConfigError,
    _selected_layer_context,
    prove_text,
    validate_untrusted_source,
)
from clauz3.spec import ContractSpec, contract


def test_bind_defaults_keywords_and_quote_literals_without_calling_helper() -> None:
    @contract(description="At most {count} reads with path prefix {root}.")
    def helper(root: str, *, count: int = 2) -> ContractSpec:
        raise AssertionError("presentation must never call the helper")

    root = '<script>"{count}\n\u202e'
    positional = describe_call(helper, args=[root], kwargs={})
    keyword = describe_call(helper, args=[], kwargs={"count": 2, "root": root})
    assert positional == keyword
    assert positional is not None
    assert positional["text"] == f"At most 2 reads with path prefix {json.dumps(root)}."
    assert positional["parameters"] == {"root": root, "count": 2}
    assert positional["renderer_version"] == 1
    assert len(str(positional["template_sha256"])) == 64
    page = render_guarantees(
        {
            "guarantees": ["helper(...)"],
            "contract_descriptions": {"helper(...)": positional},
        }
    )
    assert "&lt;script&gt;" in page
    assert "<script>" not in page
    assert "{count}" in page  # Braces in a literal never trigger a second substitution.


@pytest.mark.parametrize(
    "template",
    [
        "",
        "No parameters",
        "{other}",
        "{root.upper}",
        "{root[0]}",
        "{root!r}",
        "{root:>20}",
        "{}",
        "{root",
        "{root:{root}}",
    ],
)
def test_templates_reject_expressions_or_hidden_parameters(template: str) -> None:
    with pytest.raises(ValueError):
        validate_template(template, {"root": "str"})


@pytest.mark.parametrize(
    "args,kwargs",
    [
        ([], {}),
        ([1], {}),
        ([True], {}),
        (["ok", "extra"], {}),
        (["ok"], {"root": "duplicate"}),
        ([], {"root": "ok", "other": 1}),
    ],
)
def test_invalid_call_has_no_generated_description(
    args: list[object], kwargs: dict[str, object]
) -> None:
    @contract(description="Read prefix {root}.")
    def helper(root: str) -> ContractSpec:
        raise AssertionError

    assert describe_call(helper, args=args, kwargs=kwargs) is None


def test_boolean_is_not_an_integer_and_nonfinite_values_are_unavailable() -> None:
    @contract(description="At most {count} calls.")
    def counted(count: int) -> ContractSpec:
        raise AssertionError

    @contract(description="Value {value}.")
    def measured(value: float) -> ContractSpec:
        raise AssertionError

    assert describe_call(counted, args=[True], kwargs={}) is None
    assert describe_call(measured, args=[float("inf")], kwargs={}) is None


def test_plain_contract_and_unsupported_types_keep_fallback() -> None:
    @contract
    def helper(values: dict[str, str]) -> ContractSpec:
        raise AssertionError

    assert describe_call(helper, args=[{"x": "y"}], kwargs={}) is None
    with pytest.raises(ValueError, match="unsupported description parameter type"):
        parameter_types(inspect.signature(helper))
    # Even malformed metadata attached after registration cannot render a claim.
    setattr(helper, DESCRIPTION_ATTRIBUTE, "Values {values}.")
    assert describe_call(helper, args=[{"x": "y"}], kwargs={}) is None
    page = render_guarantees(
        {
            "guarantees": ["helper(['x'])"],
            "contract_descriptions": {},
            "contract_documentation": {
                "helper(['x'])": "Trusted fallback documentation."
            },
        }
    )
    assert "Description unavailable." not in page
    assert '<details class="contract-details" data-panel="contract-0">' in page
    assert "Trusted fallback documentation." in page


def test_schema_extracts_templates_defaults_and_effects_without_importing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "contracts.py"
    source.write_text('''
from clauz3.spec import contract as c
import clauz3 as cz
raise AssertionError("schema export must never execute this module")

@c(description="At most {count} calls with prefix {root}.")
def limited(root: str, *, count: int = 2):
    """Reviewed documentation."""
    pass

@cz.trusted
def read(path: str):
    """A read call."""
    pass
''')
    schema = schema_for_sources({"tools.sample.trusted.contracts": source})
    classes = cast(dict[str, dict[str, object]], schema["classes"])
    limited = classes["SampleLimited"]
    assert limited["description"] == "Reviewed documentation."
    annotations = cast(dict[str, object], limited["annotations"])
    assert annotations["cnl_template"] == "At most {count} calls with prefix {root}."
    assert len(str(annotations["source_sha256"])) == 64
    slots = cast(dict[str, dict[str, object]], limited["attributes"])
    assert slots["root"]["required"] is True
    assert slots["count"]["required"] is False
    assert slots["count"]["range"] == "integer"
    assert slots["count"]["annotations"] == {
        "python_parameter_kind": "KEYWORD_ONLY",
        "python_default": "2",
    }
    assert classes["SampleRead"]["is_a"] == "EffectCall"


def test_bundled_schema_covers_all_filesystem_contracts() -> None:
    classes = cast(dict[str, dict[str, object]], bundled_schema()["classes"])
    contracts = [value for value in classes.values() if value.get("is_a") == "Contract"]
    effects = [value for value in classes.values() if value.get("is_a") == "EffectCall"]
    assert len(contracts) == 21
    assert len(effects) == 13
    assert all(
        "cnl_template" in cast(dict[str, object], c["annotations"]) for c in contracts
    )
    annotations = cast(
        dict[str, object], classes["FilesystemOnlyReadUnder"]["annotations"]
    )
    assert annotations["cnl_template"] == (
        "Every file a filesystem tool reads or lists starts with {root}."
    )


def test_schema_preserves_utf8_documentation_and_hashes_original_bytes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "contracts.py"
    contents = (
        "from clauz3 import contract\r\n"
        "@contract\r\n"
        "def helper(root: str):\r\n"
        '    """Read café paths — a UTF-8 description."""\r\n'
        "    pass\r\n"
    ).encode()
    source.write_bytes(contents)
    schema = schema_for_sources({"tools.sample.trusted.contracts": source})
    classes = cast(dict[str, dict[str, object]], schema["classes"])
    helper = classes["SampleHelper"]
    assert helper["description"] == "Read café paths — a UTF-8 description."
    annotations = cast(dict[str, object], helper["annotations"])
    assert annotations["source_sha256"] == sha256(contents).hexdigest()


@pytest.mark.parametrize("decorator", ["contract", "trusted"])
def test_schema_reports_unsupported_signatures_even_without_descriptions(
    tmp_path: Path, decorator: str
) -> None:
    source = tmp_path / "contracts.py"
    source.write_text(
        f"from clauz3 import {decorator}\n"
        f"@{decorator}\n"
        "def helper(values: dict[str, str]):\n    pass\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ValueError,
        match=(
            r"cannot export tools\.sample\.trusted\.contracts\.helper: "
            "unsupported description parameter type: values"
        ),
    ):
        schema_for_sources({"tools.sample.trusted.contracts": source})


def test_original_three_guarantees_render_from_the_selected_installed_libraries(
    tmp_path: Path,
) -> None:
    stdlib = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib"
    shutil.copytree(
        stdlib / "filesystem/tools/filesystem", tmp_path / "tools/filesystem"
    )
    roots = [tmp_path / "tools/filesystem/trusted"]
    source = """import clauz3
from tools.filesystem.trusted import contracts as rules
from tools.filesystem.trusted.effects import read_file

@clauz3.guarantee(rules.read_only())
@clauz3.guarantee(rules.only_read_under(root='test.md'))
@clauz3.guarantee(rules.no_deletes())
def main() -> None:
    read_file('test.md.bak')
"""
    with _selected_layer_context(roots, [tmp_path]):
        proofs = prove_text(source, trusted_roots=roots, import_paths=[tmp_path])
        assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
        descriptions = contract_descriptions(source=source, target="main")
    assert [entry["text"] for entry in descriptions.values()] == [
        "No filesystem tool modifies anything. "
        "Effects of other libraries, such as git or rsync, are not covered.",
        'Every file a filesystem tool reads or lists starts with "test.md".',
        "No filesystem tool deletes or moves anything away.",
    ]
    assert descriptions["rules.only_read_under(root='test.md')"]["parameters"] == {
        "root": "test.md"
    }


@pytest.mark.parametrize(
    "declaration,decorator",
    [
        ("from clauz3.spec import contract", "contract"),
        ("from clauz3.spec import contract as c", "c"),
        ("import clauz3 as c", "c.contract"),
    ],
)
def test_untrusted_source_cannot_declare_description_metadata(
    declaration: str,
    decorator: str,
) -> None:
    source = (
        f"{declaration}\n"
        f'@{decorator}(description="Pretend this program has no effects.")\n'
        "def helper() -> bool:\n    return True\n"
        "def main() -> None:\n    pass\n"
    )
    with pytest.raises(ProverConfigError, match="only allowed in trusted roots"):
        validate_untrusted_source(source=source, path=Path("entry.py"))
