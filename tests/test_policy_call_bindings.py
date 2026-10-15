"""Policy triggers follow lexical callable bindings and the selected entry."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.policy import DomainPolicy, compute_coverage, transitive_calls
from clauz3.prover import prove_text
from clauz3.runner import build_approval_request

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"
_IMPORT = "from tools.email.trusted.effects import send_email\n"
_REQUIRED_UNIQUE = DomainPolicy(
    when_used=("send_email",),
    required=("unique_recipients",),
    domain_package="tools.email.trusted",
)


def _calls(source: str) -> set[str]:
    return transitive_calls(
        source, "main", import_roots=[EMAIL_ROOT], trusted_roots=[TRUSTED_ROOT]
    )


def _coverage(source: str, policies: Sequence[DomainPolicy]) -> list[dict[str, object]]:
    return compute_coverage(
        source=source,
        guarantees=[],
        policies=policies,
        import_roots=[EMAIL_ROOT],
        trusted_roots=[TRUSTED_ROOT],
    )


def test_ordinary_argument_and_return_values_do_not_trigger_policies() -> None:
    source = (
        "def identity(value: str) -> str:\n"
        "    return value\n"
        "def main() -> None:\n"
        "    assert identity('hello') == 'hello'\n"
    )
    assert _calls(source) == {"identity"}
    assert _coverage(source, [_REQUIRED_UNIQUE]) == []


_REQUIRED_AUDIT = DomainPolicy(
    when_used=("audit",),
    required=("logged",),
    domain_package="tools.audit.trusted",
)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("range", "2"),
        ("str", "1"),
        ("int", "'1'"),
        ("float", "1"),
        ("bool", "1"),
        ("list", ""),
        ("dict", ""),
        ("set", ""),
    ],
)
def test_builtin_constructor_does_not_activate_effect_policies(
    name: str, arguments: str
) -> None:
    source = f"def main() -> None:\n    value = {name}({arguments})\n"

    assert _calls(source) == {name}
    assert _coverage(source, [_REQUIRED_UNIQUE, _REQUIRED_AUDIT]) == []


@pytest.mark.parametrize(
    "binding", ["convert = str", "from builtins import str as convert"]
)
def test_builtin_constructor_alias_does_not_activate_effect_policies(
    binding: str,
) -> None:
    source = binding + "\ndef main() -> None:\n    assert convert(1) == '1'\n"

    assert _calls(source) == {"str"}
    assert _coverage(source, [_REQUIRED_UNIQUE, _REQUIRED_AUDIT]) == []


def test_builtin_range_does_not_activate_an_unrelated_effect_policy() -> None:
    source = (
        _IMPORT + "def main() -> None:\n"
        "    for index in range(1):\n"
        '        send_email("bob@example.com", "hello")\n'
    )

    assert _calls(source) == {"range", "send_email"}
    assert _coverage(source, [_REQUIRED_UNIQUE, _REQUIRED_AUDIT]) == [
        {
            "domain": "send_email",
            "status": "required_gap",
            "missing_required": ["unique_recipients"],
            "missing_recommended": [],
        }
    ]


def test_imported_effect_shadowing_a_constructor_keeps_its_policy() -> None:
    source = (
        "from tools.email.trusted.effects import send_email as str\n"
        'def main() -> None:\n    str("bob@example.com", "hello")\n'
    )

    assert _calls(source) == {"send_email"}
    report = _coverage(source, [_REQUIRED_UNIQUE, _REQUIRED_AUDIT])
    assert [entry["domain"] for entry in report] == ["send_email"]
    assert report[0]["missing_required"] == ["unique_recipients"]


@pytest.mark.parametrize(
    "body",
    [
        "class str:\n    pass\ndef main() -> None:\n    str()\n",
        "def invoke(str) -> None:\n"
        '    str("bob@example.com", "hello")\n'
        "def main() -> None:\n    invoke(send_email)\n",
    ],
    ids=["user-class", "callback-parameter"],
)
def test_shadowed_constructor_name_does_not_hide_a_dynamic_call(body: str) -> None:
    source = _IMPORT + body

    report = _coverage(source, [_REQUIRED_UNIQUE, _REQUIRED_AUDIT])

    assert [entry["domain"] for entry in report] == ["send_email", "audit"]
    assert [entry["missing_required"] for entry in report] == [
        ["unique_recipients"],
        ["logged"],
    ]


@pytest.mark.parametrize("import_first", [True, False])
def test_called_nested_helper_resolves_its_enclosing_import(import_first: bool) -> None:
    local_import = "    from tools.email.trusted.effects import send_email as deliver\n"
    helper = '    def nested() -> None:\n        deliver("bob@example.com", "hello")\n'
    statements = local_import + helper if import_first else helper + local_import
    source = "def main() -> None:\n" + statements + "    nested()\n"
    assert _calls(source) == {"nested", "send_email"}


def test_branch_import_alternatives_are_both_retained(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit_layer"
    audit_root.mkdir()
    (audit_root / "__init__.py").write_text("")
    (audit_root / "effects.py").write_text(
        "import clauz3\n\n@clauz3.trusted\ndef audit() -> None:\n    pass\n"
    )
    source = (
        "def main(flag: bool) -> None:\n"
        "    if flag:\n"
        "        from tools.email.trusted.effects import send_email as deliver\n"
        "    else:\n"
        "        from audit_layer.effects import audit as deliver\n"
        "    deliver()\n"
    )
    assert transitive_calls(
        source,
        "main",
        import_roots=[EMAIL_ROOT, tmp_path],
        trusted_roots=[TRUSTED_ROOT, audit_root],
    ) == {"send_email", "audit"}


def test_module_alias_keeps_the_binding_captured_before_reassignment() -> None:
    source = (
        _IMPORT + "deliver = send_email\n"
        "from re import match as send_email\n"
        "def main() -> None:\n"
        '    deliver("bob@example.com", "hello")\n'
    )
    assert _calls(source) == {"send_email"}


def test_mutually_recursive_helpers_terminate_and_keep_the_effect() -> None:
    source = (
        _IMPORT + "def first() -> None:\n"
        "    second()\n"
        "def second() -> None:\n"
        "    first()\n"
        '    send_email("bob@example.com", "hello")\n'
        "def main() -> None:\n"
        "    first()\n"
    )
    assert _calls(source) == {"first", "second", "send_email"}


def test_unused_nested_helper_does_not_trigger_policy_or_coverage() -> None:
    source = (
        _IMPORT + "def main() -> None:\n"
        "    def unused() -> None:\n"
        '        send_email("bob@example.com", "hello")\n'
    )
    assert _calls(source) == set()
    assert _coverage(source, [_REQUIRED_UNIQUE]) == []


@pytest.mark.parametrize(
    "callee",
    ["factory()", 'handlers["send"]', "send_email if flag else quiet"],
    ids=["returned-callable", "subscript", "conditional"],
)
def test_unresolved_callee_keeps_every_configured_policy_in_coverage(
    callee: str,
) -> None:
    source = (
        _IMPORT + "def factory():\n"
        "    return send_email\n"
        "def quiet(to: str, body: str) -> None:\n"
        "    pass\n"
        "def main(flag: bool) -> None:\n"
        '    handlers = {"send": send_email}\n'
        f'    ({callee})("bob@example.com", "hello")\n'
    )
    report = _coverage(source, [_REQUIRED_UNIQUE, _REQUIRED_AUDIT])
    # An unresolved callable cannot establish that even an unrelated domain is
    # unused. Deliberately over-approximate rather than omit a required policy.
    assert [entry["domain"] for entry in report] == ["send_email", "audit"]
    assert [entry["status"] for entry in report] == ["required_gap", "required_gap"]
    assert [entry["missing_required"] for entry in report] == [
        ["unique_recipients"],
        ["logged"],
    ]


def test_resolved_callee_does_not_activate_an_unrelated_policy() -> None:
    source = _IMPORT + 'def main() -> None:\n    send_email("bob@example.com", "hi")\n'
    report = _coverage(source, [_REQUIRED_UNIQUE, _REQUIRED_AUDIT])
    assert [entry["domain"] for entry in report] == ["send_email"]
    assert report[0]["status"] == "required_gap"


@pytest.mark.parametrize(
    "body",
    [
        "    deliver = send_email\n"
        '    deliver("bob@example.com", "one")\n'
        '    deliver("bob@example.com", "two")\n',
        "    deliver = quiet\n"
        '    for body in ["one", "two", "three"]:\n'
        '        deliver("bob@example.com", body)\n'
        "        deliver = send_email\n",
    ],
    ids=["simple-alias", "loop-carried-alias"],
)
def test_callable_alias_cannot_suppress_a_required_contract(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies", lambda **_: [_REQUIRED_UNIQUE]
    )
    source = (
        _IMPORT + "def quiet(to: str, body: str) -> None:\n"
        "    pass\n"
        "def main() -> None:\n" + body
    )
    results = prove_text(
        source, import_paths=[EMAIL_ROOT], trusted_roots=[TRUSTED_ROOT]
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]
    assert "guarantee" in str(results[0].proof)
    assert "send_email" in _calls(source)


def test_closure_called_before_rebinding_retains_the_earlier_effect() -> None:
    source = (
        _IMPORT + "def quiet(to: str, body: str) -> None:\n"
        "    pass\n"
        "def main() -> None:\n"
        "    deliver = send_email\n"
        "    def nested() -> None:\n"
        '        deliver("bob@example.com", "one")\n'
        '        deliver("bob@example.com", "two")\n'
        "    nested()\n"
        "    deliver = quiet\n"
    )
    assert {"nested", "send_email"} <= _calls(source)


@pytest.mark.parametrize("selected_effectful", [False, True])
def test_approval_coverage_uses_the_requested_custom_target(
    selected_effectful: bool,
) -> None:
    effect = 'send_email("bob@example.com", "hello")'
    main_body = "pass" if selected_effectful else effect
    task_body = effect if selected_effectful else "pass"
    source = (
        _IMPORT + f"def main() -> None:\n    {main_body}\n"
        f"def task() -> None:\n    {task_body}\n"
    )
    proofs = prove_text(
        source,
        target="task",
        import_paths=[EMAIL_ROOT],
        trusted_roots=[TRUSTED_ROOT],
    )
    assert [(result.name, result.proof.conclusion) for result in proofs] == [
        ("task", Conclusion.OK)
    ]
    request = build_approval_request(
        source=source,
        source_name="custom_entry.py",
        target="task",
        proofs=proofs,
        trusted_roots=[TRUSTED_ROOT],
        import_roots=[EMAIL_ROOT],
    )
    expected = (
        [
            {
                "domain": "email",
                "status": "silent_gap",
                "missing_required": [],
                "missing_recommended": ["only", "unique_recipients"],
            }
        ]
        if selected_effectful
        else []
    )
    assert request["coverage"] == expected
