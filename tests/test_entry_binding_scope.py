"""Entry binding checks follow Python scopes without executing module code."""

from unittest.mock import Mock

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProverConfigError, prove_text
from clauz3.runner import RunConfigError, run_source

_MAIN = "def main() -> None:\n    pass\n"


@pytest.mark.parametrize(
    "replacement",
    [
        "if True:\n    def main() -> None:\n        pass\n",
        "if False:\n    main = 1\n",
        "for main in ():\n    pass\n",
        "while False:\n    main = 1\n",
        "try:\n    pass\nexcept Exception as main:\n    pass\n",
        "with object() as main:\n    pass\n",
        "match 1:\n    case main:\n        pass\n",
        "del main\n",
        "main += 1\n",
        "other = (main := 1)\n",
        "items = [(main := item) for item in ()]\n",
        "def helper(value=(main := 1)):\n    pass\n",
        "helper = lambda value=(main := 1): value\n",
        "class Helper((main := object)):\n    pass\n",
        "class Helper:\n    global main\n    main = 1\n",
        "class Helper:\n    class Nested:\n        global main\n        main = 1\n",
    ],
)
def test_module_scope_rebinding_cannot_hide_in_another_statement(
    replacement: str,
) -> None:
    with pytest.raises(ProverConfigError, match=r"target 'main'.*module level"):
        prove_text(_MAIN + replacement, source_name="binding.py")


@pytest.mark.parametrize(
    "separate_scope",
    [
        "def helper():\n    main = 1\n    return main\n",
        "def helper():\n    global main\n    main = 1\n",
        "helper = lambda: (main := 1)\n",
        "class Helper:\n    main = 1\n",
        "class Helper:\n    def method(self):\n        global main\n        main = 1\n",
        "items = [main for main in ()]\n",
        "items = {main: main for main in ()}\n",
        "main: int\n",
        "other.main = 1\n",
    ],
)
def test_unrelated_scopes_and_nonbinding_annotations_do_not_rebind_entry(
    separate_scope: str,
) -> None:
    # These isolate name binding, not the interpreter's broader module subset.
    # The selected function is harmless; module statements are not executed.
    results = prove_text(_MAIN + separate_scope)
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_function_global_rebinding_is_refused_before_approval_or_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The static entry check above skips function bodies. Runtime preflight
    # still refuses their unsupported global statements, even in unused helpers.
    source = _MAIN + "def helper():\n    global main\n    main = 1\n"
    approve = Mock(side_effect=AssertionError("global statement reached approval"))
    execute = Mock(side_effect=AssertionError("global statement reached execution"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute)

    with pytest.raises(RunConfigError, match="Global is not part of the proved subset"):
        run_source(
            source,
            source_name="binding.py",
            trusted_roots=[],
            import_roots=[],
            approval_service_url="http://approval.invalid",
        )
    approve.assert_not_called()
    execute.assert_not_called()


@pytest.mark.parametrize("target", ["main", None])
def test_conditional_only_entry_is_not_a_direct_module_definition(
    target: str | None,
) -> None:
    with pytest.raises(ProverConfigError, match=r"target 'main'.*directly"):
        prove_text("if True:\n    def main() -> None:\n        pass\n", target=target)


@pytest.mark.parametrize("before", [True, False])
def test_wildcard_import_refused_on_either_side_of_the_entry(before: bool) -> None:
    wildcard = "from math import *\n"
    source = wildcard + _MAIN if before else _MAIN + wildcard
    with pytest.raises(ProverConfigError, match="wildcard"):
        prove_text(source)


def test_future_import_alias_is_a_real_module_binding() -> None:
    with pytest.raises(ProverConfigError, match=r"target 'main'.*module level"):
        prove_text("from __future__ import annotations as main\n" + _MAIN)


@pytest.mark.parametrize("target", ["main", None])
def test_class_staticmethod_is_not_a_module_entry(
    target: str | None,
) -> None:
    source = "class Ops:\n    @staticmethod\n    def main() -> None:\n        pass\n"
    assert prove_text(source, target=target) == []


@pytest.mark.parametrize("target", ["main", None])
def test_assignment_staticmethod_is_not_a_module_entry(target: str | None) -> None:
    source = (
        "class Ops:\n"
        "    def main() -> None:\n"
        "        pass\n"
        "    main = staticmethod(main)\n"
    )
    assert prove_text(source, target=target) == []


@pytest.mark.parametrize("target", ["main", None])
def test_assignment_staticmethod_does_not_compete_with_module_entry(
    target: str | None,
) -> None:
    source = (
        _MAIN + "class Ops:\n"
        "    def main() -> None:\n"
        "        assert False\n"
        "    main = staticmethod(main)\n"
    )
    results = prove_text(source, target=target)
    assert [(result.name, result.proof.conclusion) for result in results] == [
        ("main", Conclusion.OK)
    ]


@pytest.mark.parametrize("target", ["__new__", None])
def test_class_new_method_is_not_a_module_entry(target: str | None) -> None:
    source = "class Ops:\n    def __new__(cls: int) -> int:\n        return cls\n"
    assert prove_text(source, target=target) == []


def test_class_new_method_is_not_included_with_module_entries() -> None:
    source = (
        _MAIN + "class Ops:\n    def __new__(cls: int) -> int:\n        return cls\n"
    )
    results = prove_text(source, target=None)
    assert [(result.name, result.proof.conclusion) for result in results] == [
        ("main", Conclusion.OK)
    ]
