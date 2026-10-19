"""Text prefixes work without weakening proof guarantees or Python semantics."""

from pathlib import Path

import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._proxies import IntSort, StrSort
from clauz3._vendor.deal_solver._proxies._none import NoneSort
from clauz3._vendor.deal_solver._strings import string_sort, string_val
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/clauz3/stdlib/web_fetch"
TRUSTED = WEB / "tools/web_fetch/trusted"


def check(source: str, expected: Conclusion = Conclusion.OK) -> None:
    results = prove_text(source)
    assert [r.proof.conclusion for r in results] == [expected], [
        str(r.proof) for r in results
    ]


@pytest.mark.parametrize(
    ("text", "arguments", "expected"),
    [
        ("a\nb\nc\nd\ne\nf", '"\\n"', ["a", "b", "c", "d", "e", "f"]),
        ("", '","', [""]),
        (",a,,b,", '","', ["", "a", "", "b", ""]),
        ("aaaaa", '"aa"', ["", "", "a"]),
        ("a<>b<>c", '"<>", 1', ["a", "b<>c"]),
        ("a,b", 'maxsplit=0, sep=","', ["a,b"]),
        ("a,b,c", 'sep=",", maxsplit=-2', ["a", "b", "c"]),
        ("a,b,c", '",", True', ["a", "b,c"]),
        ("", "", []),
        (" \t\r\n", "", []),
        (" a\t b\n c ", "", ["a", "b", "c"]),
        ("  a b  ", "None, 0", ["a b  "]),
        ("  a b  ", "maxsplit=1", ["a", "b  "]),
        ("\u00a0Ω\u2003😀\x1cend", "sep=None", ["Ω", "😀", "end"]),
        ("Ω😀Ω\0\ud800\U0010ffff", '"Ω"', ["", "😀", "\0\ud800\U0010ffff"]),
    ],
)
def test_split_literals_match_python(
    text: str, arguments: str, expected: list[str]
) -> None:
    expression = f"{ascii(text)}.split({arguments})"
    source = f"def main() -> None:\n    assert {expression} == {ascii(expected)}\n"
    check(source)
    execute(source, source_name="split.py", policy=build_policy())
    check(
        f"def main() -> None:\n    assert len({expression}) == {len(expected) + 1}\n",
        Conclusion.FAIL,
    )


@pytest.mark.parametrize(
    "value", ["", "Ω😀\0\ud800\U0010ffff", [], [1, 2, 3], (), (1, 2, 3)]
)
@pytest.mark.parametrize(
    "bounds",
    [
        ":",
        ":5",
        "-2:",
        ":-1",
        "-99:99",
        "99:",
        "2:1",
        "None:None",
        "True:False",
        "::1",
        "::None",
    ],
)
def test_slices_match_python(
    value: str | list[int] | tuple[int, ...], bounds: str
) -> None:
    expression = f"{ascii(value)}[{bounds}]"
    expected = eval(expression, {"__builtins__": {}})
    source = f"def main() -> None:\n    assert {expression} == {ascii(expected)}\n"
    check(source)
    execute(source, source_name="slice.py", policy=build_policy())


@pytest.mark.parametrize("value", ["Ω😀\0\U0010ffff", [1, 2, 3, 4], (1, 2, 3, 4)])
@pytest.mark.parametrize("index", [-4, -1, 0, True])
def test_indexing_matches_python(
    value: str | list[int] | tuple[int, ...], index: int
) -> None:
    source = (
        "def main() -> None:\n"
        f"    assert {ascii(value)}[{index}] == {ascii(value[index])}\n"
    )
    check(source)
    execute(source, source_name="index.py", policy=build_policy())


@pytest.mark.parametrize(
    "expression",
    [
        '"abc"[3]',
        '"abc"[-4]',
        '""[0]',
        "[1][-2]",
        "(1,)[1]",
        '"abc"[1.2]',
        '"abc"["x":]',
        '[]["x":]',
        "()[1.5:]",
        '"abc".split("")',
        '"abc".split(",", 1267650600228229401496703205376)',
    ],
)
def test_invalid_operations_do_not_prove(expression: str) -> None:
    check(f"def main() -> None:\n    print({expression})\n", Conclusion.FAIL)


@pytest.mark.parametrize(
    "expression",
    [
        "s.split(1)",
        "s.split(maxsplit=None)",
        's.split(",", 1, 2)',
        's.split(",", sep=",")',
        "s.split(unknown=1)",
        "s.split(ctx=1)",
        "s.split.__call__(ctx=1)",
        "s[::2]",
        "s[::-1]",
    ],
)
def test_unsupported_bindings_fail_closed(expression: str) -> None:
    check(f"def main(s: str) -> None:\n    print({expression})\n", Conclusion.SKIP)


@pytest.mark.parametrize(
    "assertion",
    [
        "len(s[:5]) <= 5",
        "len(s[-5:]) <= 5",
        "s[:] == s",
        "s[-len(s):] == s",
        'len(s.split(",")) >= 1',
        'len(s.split(",")) <= len(s) + 1',
        'len(s.split(",", 4)[:5]) <= 5',
        's.split(",", 0) == [s]',
        's.split(",") == s.split(",")',
        "len(s.split()) <= len(s)",
        "len(s.split(maxsplit=4)) <= 5",
    ],
)
def test_symbolic_bounds(assertion: str) -> None:
    check(f"def main(s: str) -> None:\n    assert {assertion}\n")


@pytest.mark.parametrize(
    ("text", "separator", "limit"),
    [
        ("", ",", -1),
        ("", None, -1),
        ("  ", None, 0),
        (" a  b  ", None, 1),
        (" a b", None, -2),
        (",a,,b,", ",", -1),
        ("aaaaa", "aa", 1),
        ("abc", "missing", 5),
        ("a,b,c", ",", 0),
        ("a,b,c", ",", 1),
        ("\u00a0Ω\u2003😀", None, 8),
        ("Ω\ud800\0\U0010ffffΩ", "Ω", -1),
    ],
)
def test_symbolic_split_model_admits_actual_python_result(
    text: str, separator: str | None, limit: int
) -> None:
    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    receiver = StrSort.var(name="text", ctx=z3.main_ctx())
    sep = (
        NoneSort() if separator is None else StrSort.var(name="sep", ctx=z3.main_ctx())
    )
    maximum = IntSort.var(name="maximum", ctx=z3.main_ctx())
    result = receiver.r_split(sep, maximum, ctx=ctx)
    actual = z3.Empty(z3.SeqSort(string_sort()))
    for part in text.split(separator, limit):
        actual = z3.Concat(actual, z3.Unit(string_val(part)))
    solver = z3.Solver()
    solver.set(timeout=5000)
    solver.add(*(term.expr for term in ctx.given))
    solver.add(
        receiver.expr == string_val(text), maximum.expr == limit, result.expr == actual
    )
    if separator is not None:
        solver.add(sep.expr == string_val(separator))
    assert solver.check() == z3.sat


def test_empty_symbolic_separator_and_large_maxsplit_cannot_hide_failure() -> None:
    for condition, operation in [
        ('sep == ""', "s.split(sep)"),
        ("maximum == 1267650600228229401496703205376", 's.split(",", maximum)'),
    ]:
        check(
            "def main(s: str, sep: str, maximum: int) -> None:\n"
            f"    if {condition}:\n        print({operation})\n",
            Conclusion.FAIL,
        )


def test_split_lists_are_independent_but_alias_mutations_are_refused() -> None:
    check(
        'def main() -> None:\n    a = "a,b".split(",")\n'
        '    b = "a,b".split(",")\n    a.append("c")\n'
        '    assert b == ["a", "b"]\n'
    )
    check(
        'def main() -> None:\n    a = "a,b".split(",")\n'
        '    b = a\n    a.clear()\n    assert b == ["a", "b"]\n',
        Conclusion.SKIP,
    )


def web_program(body: str) -> str:
    return (
        "import clauz3\n"
        "from tools.web_fetch.trusted import contracts as web\n"
        "from tools.web_fetch.trusted.effects import fetch_url, post_url\n"
        '@clauz3.guarantee(web.only_fetch_under("https://example.org/"))\n'
        "@clauz3.guarantee(web.https_only())\n"
        "@clauz3.guarantee(web.fetches_at_most(1))\n"
        "@clauz3.guarantee(web.no_posts())\n"
        "def main() -> None:\n"
        '    text = fetch_url("https://example.org/entry.txt")\n'
        + "".join(f"    {line}\n" for line in body.splitlines())
    )


def test_handoff_workflow_proves_and_prints_only_five_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    program = web_program('lines = text.split("\\n")\nprint("first5:", lines[:5])')
    results = prove_text(program, import_paths=[WEB], trusted_roots=[TRUSTED])
    assert all(r.ok for r in results), [str(r.proof) for r in results]
    execute(
        program,
        source_name="head.py",
        policy=build_policy(trusted_roots=[TRUSTED], import_roots=[WEB]),
        import_roots=[WEB],
        trusted_dispatch=lambda q, f, a, k: (
            "one\ntwo\nthree\nfour\nfive\nDO NOT PRINT\n"
        ),
    )
    assert (
        capsys.readouterr().out == "first5: ['one', 'two', 'three', 'four', 'five']\n"
    )


@pytest.mark.parametrize(
    "condition", ['text[:5] == "allow"', 'text.split(",")[0] == "allow"']
)
def test_processing_text_cannot_hide_forbidden_effects(condition: str) -> None:
    program = web_program(
        f'if {condition}:\n    post_url("https://example.org/send", "body")'
    )
    results = prove_text(program, import_paths=[WEB], trusted_roots=[TRUSTED])
    assert not all(r.ok for r in results), [str(r.proof) for r in results]
