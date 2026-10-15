"""Trusted string-list iteration covers every element without unrolling."""

from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._exceptions import ProveError, UnsupportedError
from clauz3.approvals.client import MockApprovalServer
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import ProofResult, prove_text
from clauz3.runner import RunProofError, run_source


@pytest.fixture
def layer(tmp_path: Path) -> Path:
    root = tmp_path / "strings"
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "effects.py").write_text("""import clauz3

@clauz3.trusted
@clauz3.ensures(lambda result: len(result) <= 3)
def values() -> list[str]:
    return ["allowed", "forbidden", "allowed"]

@clauz3.trusted
@clauz3.ensures(lambda result: len(result) == 0)
def empty() -> list[str]:
    return []

@clauz3.trusted
@clauz3.ensures(lambda result: len(result) <= 2)
def children(parent: str) -> list[str]:
    return [parent, "child"]

@clauz3.trusted
@clauz3.ensures(lambda parent, result: result == [parent])
def echo(parent: str) -> list[str]:
    return [parent]

class Item(clauz3.Row):
    text: str

@clauz3.trusted
@clauz3.ensures(lambda result: len(result) == 0)
def empty_rows() -> list[Item]:
    return []

@clauz3.trusted
def emit(value: str) -> None:
    print(value)

@clauz3.trusted
@clauz3.requires(lambda value: value == "allowed")
def restricted(value: str) -> None:
    print(value)

@clauz3.trusted
def mutate(items: list[str]) -> None:
    items.append("forbidden")

@clauz3.trusted
def mutate_nested(items: list[list[str]]) -> None:
    items[0].append("forbidden")

@clauz3.trusted
def pair(left: str, right: str) -> None:
    print(left, right)
""")
    (root / "contracts.py").write_text("""from clauz3.spec import contract, effect

@contract
def only(value: str):
    return effect("emit").all(lambda call: call.value == value)

@contract
def at_most(count: int):
    return effect("emit").count() <= count

@contract
def none():
    return effect("emit").empty()

@contract
def matching_pairs():
    return effect("pair").all(lambda call: call.left == call.right)

@contract
def unique():
    return effect("emit").distinct(lambda call: call.value)
""")
    return root


def source(body: str, guarantee: str = "rules.at_most(3)") -> str:
    return (
        "import clauz3\n"
        "from strings.effects import (values, empty, children, echo, emit, "
        "restricted, mutate, mutate_nested, pair, empty_rows)\n"
        "from strings import contracts as rules\n"
        f"@clauz3.guarantee({guarantee})\n"
        "def main() -> None:\n"
        + "\n".join("    " + line for line in body.splitlines())
        + "\n"
    )


def prove(layer: Path, program: str) -> ProofResult:
    (result,) = prove_text(program, trusted_roots=[layer], import_paths=[layer.parent])
    return result


def assert_refuted(layer: Path, program: str) -> None:
    result = prove(layer, program)
    assert result.proof.conclusion is Conclusion.FAIL, result.proof


def assert_sequence_solver_incomplete(
    layer: Path, program: str, conclusion: Conclusion = Conclusion.SKIP
) -> None:
    # Z3 cannot currently decide these quantified sequence counterexamples.
    # Pin that limitation explicitly: an unsupported AST or encoding error
    # must not count as a successful negative test.
    result = prove(layer, program)
    assert result.proof.conclusion is conclusion, result.proof
    assert isinstance(result.proof.error, ProveError)


def test_iterates_all_strings_and_executes(
    layer: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    program = source("for item in values():\n    emit(item)")
    result = prove(layer, program)
    assert result.ok, result.proof
    execute(
        program,
        source_name="scalar.py",
        policy=build_policy(trusted_roots=[layer], import_roots=[layer.parent]),
        import_roots=[layer.parent],
    )
    assert capsys.readouterr().out.splitlines() == ["allowed", "forbidden", "allowed"]


@pytest.mark.parametrize("guarantee", ["rules.at_most(1)", 'rules.only("allowed")'])
def test_false_guarantees_cover_every_element(layer: Path, guarantee: str) -> None:
    program = source("for item in values():\n    emit(item)", guarantee)
    if guarantee == "rules.at_most(1)":
        assert_refuted(layer, program)
    else:
        assert_sequence_solver_incomplete(layer, program)


def test_false_universal_guarantee_is_refuted(layer: Path) -> None:
    assert_refuted(
        layer,
        source('for item in values():\n    emit("forbidden")', 'rules.only("allowed")'),
    )


@pytest.mark.parametrize("guarantee", ["rules.at_most(1)", 'rules.only("allowed")'])
def test_false_guarantees_cannot_request_approval(
    layer: Path, guarantee: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_execution(*args: object, **kwargs: object) -> None:
        pytest.fail("unproved iteration reached execution")

    monkeypatch.setattr("clauz3.runner._execute_source", forbidden_execution)
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        with pytest.raises(RunProofError):
            run_source(
                source("for item in values():\n    emit(item)", guarantee),
                source_name="scalar.py",
                trusted_roots=[layer],
                import_roots=[layer.parent],
                approval_service_url=server.url,
            )
        assert server.requests == []


def test_branch_guard_applies_to_each_precondition(layer: Path) -> None:
    result = prove(
        layer,
        source(
            'for item in values():\n    if item == "allowed":\n        restricted(item)'
        ),
    )
    assert result.ok, result.proof
    assert_sequence_solver_incomplete(
        layer, source("for item in values():\n    restricted(item)"), Conclusion.PARTIAL
    )


def test_nested_iteration_retains_both_indices(layer: Path) -> None:
    body = (
        "for parent in values():\n    for child in children(parent):\n"
        "        if child == parent:\n            emit(parent)"
    )
    result = prove(layer, source(body, "rules.at_most(6)"))
    assert result.ok, result.proof
    assert_sequence_solver_incomplete(layer, source(body, "rules.at_most(1)"))


def test_returned_lists_can_differ_between_outer_iterations(
    layer: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    program = source(
        "for parent in values():\n    for child in echo(parent):\n"
        "        pair(parent, child)",
        "rules.matching_pairs()",
    )
    result = prove(layer, program)
    assert result.ok, result.proof
    assert_sequence_solver_incomplete(
        layer, program.replace("pair(parent, child)", 'pair("wrong", child)')
    )
    execute(
        program,
        source_name="nested.py",
        policy=build_policy(trusted_roots=[layer], import_roots=[layer.parent]),
        import_roots=[layer.parent],
    )
    assert capsys.readouterr().out.splitlines() == [
        "allowed allowed",
        "forbidden forbidden",
        "allowed allowed",
    ]


def test_empty_loop_does_not_raise_or_hide_later_effects(layer: Path) -> None:
    body = 'for item in empty():\n    raise ValueError()\nemit("forbidden")'
    assert_refuted(layer, source(body, "rules.none()"))
    result = prove(layer, source(body, 'rules.only("forbidden")'))
    assert result.ok, result.proof


@pytest.mark.parametrize("query", ["empty", "empty_rows"])
def test_allowed_exception_in_empty_loop_does_not_hide_effect(
    layer: Path, query: str
) -> None:
    program = source(
        f'for item in {query}():\n    raise ValueError()\nemit("forbidden")',
        "rules.none()",
    )
    program = "import deal\n" + program.replace(
        "def main()", "@deal.raises(ValueError)\ndef main()"
    )
    assert_refuted(layer, program)


@pytest.mark.parametrize("query", ["empty", "empty_rows"])
def test_empty_loop_exception_does_not_make_helper_total(
    layer: Path, query: str
) -> None:
    program = source('if maybe(False) != False:\n    emit("forbidden")', "rules.none()")
    helper = (
        "def maybe(flag):\n    if flag:\n        return False\n"
        f"    for item in {query}():\n        raise ValueError()\n"
    )
    program = program.replace("@clauz3.guarantee", helper + "\n@clauz3.guarantee")
    program = "import deal\n" + program.replace(
        "def main()", "@deal.raises(ValueError)\ndef main()"
    )
    result = prove(layer, program)
    assert result.proof.conclusion is Conclusion.SKIP
    assert "implicitly return None" in str(result.proof.error)


def test_reached_exception_refuses_the_proof(layer: Path) -> None:
    assert_refuted(layer, source("for item in values():\n    raise ValueError()"))


@pytest.mark.parametrize(
    "body",
    [
        'for item in values():\n    if item == "unreachable":\n        fail()'
        '\nemit("forbidden")',
        'for item in empty():\n    fail()\nemit("forbidden")',
        "for parent in values():\n    for child in empty():\n        fail()"
        '\nemit("forbidden")',
    ],
)
def test_conditional_nested_and_helper_exceptions_do_not_hide_effects(
    layer: Path, body: str, capsys: pytest.CaptureFixture[str]
) -> None:
    program = source(body, "rules.none()")
    program = program.replace(
        "@clauz3.guarantee", "def fail():\n    raise ValueError()\n\n@clauz3.guarantee"
    )
    execute(
        program,
        source_name="exceptions.py",
        policy=build_policy(trusted_roots=[layer], import_roots=[layer.parent]),
        import_roots=[layer.parent],
    )
    assert capsys.readouterr().out.splitlines() == ["forbidden"]
    program = "import deal\n" + program.replace(
        "def main()", "@deal.raises(ValueError)\ndef main()"
    )
    assert_refuted(layer, program)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "count = 0\nfor item in values():\n    count = count + 1",
            "name bound outside",
        ),
        (
            "for item in values():\n    local = item\nemit(local)",
            "cannot resolve callable target local",
        ),
        (
            'item = "allowed"\nfor item in values():\n    pass\nemit(item)',
            "shadows an existing binding",
        ),
        (
            'items = values()\nalias = items\nalias.append("forbidden")\n'
            "for item in items:\n    emit(item)",
            "unsupported container operation",
        ),
        (
            'items = values()\nalias = items\nalias[0] = "forbidden"\n'
            "for item in items:\n    emit(item)",
            "cannot mutate a trusted string-list return",
        ),
        (
            "items = values()\nmutate(items)\nfor item in items:\n    emit(item)",
            "passing a trusted string-list return",
        ),
        (
            "items = values()\nmutate_nested([items])\n"
            "for item in items:\n    emit(item)",
            "embedding trusted string-list returns",
        ),
        (
            "items = []\nfor item in values():\n    items.append(item)",
            "unsupported container operation",
        ),
        ("for item in values():\n    break", "break inside a for-loop"),
    ],
)
def test_unsupported_state_is_refused(layer: Path, body: str, message: str) -> None:
    result = prove(layer, source(body))
    assert result.proof.conclusion is Conclusion.SKIP, result.proof
    assert isinstance(result.proof.error, UnsupportedError)
    assert message in str(result.proof.error)


@pytest.mark.parametrize(
    ("body", "diagnostic"),
    [
        (
            "items = []\nfor row in empty_rows():\n    items.append(row)",
            "container",
        ),
        (
            'items = {}\nfor row in empty_rows():\n    items["x"] = row',
            "subscript assignment inside a quantified loop is not supported",
        ),
    ],
)
def test_row_loops_also_refuse_container_mutation(
    layer: Path, body: str, diagnostic: str
) -> None:
    result = prove(layer, source(body))
    assert result.proof.conclusion is Conclusion.SKIP, result.proof
    assert isinstance(result.proof.error, UnsupportedError)
    assert diagnostic in str(result.proof.error)


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("two_sites", [False, True])
def test_distinct_over_empty_results(
    layer: Path, nested: bool, two_sites: bool
) -> None:
    body = "for child in empty():\n    emit(child)"
    if two_sites:
        body += "\n    emit(child)"
    if nested:
        body = "for parent in values():\n" + "\n".join(
            "    " + line for line in body.splitlines()
        )
    result = prove(layer, source(body, "rules.unique()"))
    assert result.proof.conclusion is Conclusion.OK, result.proof


def test_assignment_preserves_iteration_provenance(layer: Path) -> None:
    result = prove(
        layer,
        source("items = values()\nalias = items\nfor item in alias:\n    emit(item)"),
    )
    assert result.ok, result.proof


@pytest.mark.parametrize(
    "selected",
    [
        "([items] + [inner])[1]",
        "([items] + [inner])[1:][0]",
        "[items, inner][1]",
        "(items, inner)[1]",
        "([inner] + [items])[0]",
    ],
)
@pytest.mark.parametrize("guarantee", ["rules.at_most(1)", 'rules.only("a")'])
def test_nested_containers_cannot_mint_iteration_provenance(
    layer: Path,
    selected: str,
    guarantee: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program = source(
        'items = values()\ninner = ["a"]\nalias = '
        + selected
        + '\ninner.append("b")\nfor item in alias:\n    emit(item)',
        guarantee,
    )
    # Concrete aliasing violates both guarantees. This must be refused, even
    # when a container's element factory reconstructs TrustedStringListSort.
    execute(
        program,
        source_name="alias.py",
        policy=build_policy(trusted_roots=[layer], import_roots=[layer.parent]),
        import_roots=[layer.parent],
    )
    assert capsys.readouterr().out.splitlines() == ["a", "b"]
    result = prove(layer, program)
    assert result.proof.conclusion is Conclusion.SKIP, result.proof
    assert isinstance(result.proof.error, UnsupportedError)

    def forbidden_execution(*args: object, **kwargs: object) -> None:
        pytest.fail("derived-list iteration reached execution")

    monkeypatch.setattr("clauz3.runner._execute_source", forbidden_execution)
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        with pytest.raises(RunProofError):
            run_source(
                program,
                source_name="alias.py",
                trusted_roots=[layer],
                import_roots=[layer.parent],
                approval_service_url=server.url,
            )
        assert server.requests == []


@pytest.mark.parametrize(
    "setup",
    [
        "alias = ([inner] + [items])[1]",
        "alias = ([items] + [inner])[0]",
        "alias = [inner, items][1]",
        "alias = (inner, items)[1]",
        "box = [inner]\nbox.append(items)\nalias = box[1]",
        "box = []\nbox.append(items)\nalias = box[0]",
        "box = [inner]\nbox.extend([items])\nalias = box[1]",
        'box = {"x": inner}\nbox["y"] = items\nalias = box["y"]',
        'box = {}\nbox["y"] = items\nalias = box["y"]',
        'box = {"x": inner, "y": items}\nalias = box["y"]',
        'alias = {"x": inner}.get("missing", items)',
        "box = [[inner]]\nbox.append([items])\nalias = box[1][0]",
    ],
)
@pytest.mark.parametrize("mutation", ['alias.append("forbidden")', "mutate(alias)"])
def test_container_alias_cannot_mutate_original_iteration_source(
    layer: Path, setup: str, mutation: str, capsys: pytest.CaptureFixture[str]
) -> None:
    program = source(
        'items = values()\ninner = ["a"]\n'
        + setup
        + "\n"
        + mutation
        + "\nfor item in items:\n    emit(item)",
        "rules.at_most(3)",
    )
    execute(
        program,
        source_name="erased-alias.py",
        policy=build_policy(trusted_roots=[layer], import_roots=[layer.parent]),
        import_roots=[layer.parent],
    )
    expected = ["allowed", "forbidden", "allowed"]
    if mutation.startswith("alias.append"):
        expected.append("forbidden")
    # Trusted arguments are copied by marshal_out: mutate(alias) does not
    # mutate the interpreter's list. It remains a conservative proof refusal;
    # direct alias.append is the concrete four-effect counterexample.
    assert capsys.readouterr().out.splitlines() == expected
    result = prove(layer, program)
    assert result.proof.conclusion is Conclusion.SKIP, result.proof
    assert isinstance(result.proof.error, UnsupportedError)
    assert "embedding trusted string-list returns" in str(result.proof.error)


@pytest.mark.parametrize(
    "body", ["box = {items}", "box = set()\nbox.add(items)", "box = {items: 1}"]
)
def test_hash_container_insertion_refuses_iteration_sources(
    layer: Path, body: str
) -> None:
    result = prove(layer, source("items = values()\n" + body))
    assert result.proof.conclusion is Conclusion.SKIP, result.proof
    assert isinstance(result.proof.error, UnsupportedError)
    assert "embedding trusted string-list returns" in str(result.proof.error)


def test_guarded_index_obligation_is_independent_of_other_domains() -> None:
    """Do not send hundreds of unrelated string domains with an impossible access."""
    import z3

    from clauz3._vendor.deal_solver._proxies import BoolSort
    from clauz3._vendor.deal_solver._strings import string_domain, string_sort
    from clauz3.prover import AgentTheorem

    (theorem,) = AgentTheorem.from_text("def main():\n    pass\n")
    length = z3.Int("length")
    domains = z3.And(
        *(string_domain(z3.Const(f"result_{i}", string_sort())) for i in range(200))
    )
    for index in (0, 1, 199):
        out_of_bounds = z3.And(index < length, index >= length)
        query = theorem._imply(BoolSort(domains), BoolSort(out_of_bounds))
        assert z3.is_false(query.expr)


def test_symbolic_models_replay_as_concrete_iterations(layer: Path) -> None:
    from clauz3.differential import check_source

    report = check_source(
        source('for item in values():\n    emit("allowed")', 'rules.only("allowed")'),
        source_name="scalar-replay.py",
        trusted_roots=[layer],
        import_roots=[layer.parent],
        runs=4,
        max_rows=2,
        timeout_ms=2000,
    )
    assert report.ok, report
    assert len(report.runs) >= 2
    assert any("strings.effects.emit" in repr(run.effects) for run in report.runs)
