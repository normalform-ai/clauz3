"""Containment: the escapes ``runner.py`` denylists, run through the interpreter.

``tests/test_runtime_sandbox_hardening.py`` covers the same payloads against
``clauz3.runner._validate_runtime_source``, and its own docstring calls that
approach "harm-reduction stopgaps, not a security boundary". Every rejection
there is an entry in ``FORBIDDEN_BUILTINS`` or ``FORBIDDEN_ATTRIBUTES`` that
somebody had to think of first.

The point of these tests is *how* the interpreter rejects the same payloads.
There is no denylist in ``clauz3.interp``: ``type``, ``__import__``, ``mro``,
``gi_frame`` and ``format`` are not names it refuses, they are names it has no
rule to resolve. So the assertions below are about the shape of the failure --
``NameResolutionError`` for a name that does not exist in the value domain, and
``UnsupportedNode`` for syntax with no handler.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from clauz3.imports_policy import build_policy
from clauz3.interp import (
    InterpError,
    NameResolutionError,
    Outcome,
    TrustedBoundaryError,
    UnsupportedNode,
    execute,
)

#: The denylists ``clauz3.runner`` needed while the runtime was ``exec``, kept
#: here as a catalogue of known CPython escape primitives once the production
#: lists were deleted. Each entry was added by someone who thought of a new path
#: from a live Python object back to ``os``; the point of this module is that
#: none of them is reachable now, and none is named anywhere in ``clauz3.interp``.
FORBIDDEN_BUILTINS = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "input",
        "locals",
        "open",
        "setattr",
        "vars",
        "type",
        "super",
        "object",
        "classmethod",
        "staticmethod",
        "memoryview",
        "help",
        "exit",
        "quit",
        "copyright",
        "license",
        "credits",
    }
)
FORBIDDEN_ATTRIBUTES = frozenset(
    {
        "__bases__",
        "__builtins__",
        "__class__",
        "__dict__",
        "__globals__",
        "__getattribute__",
        "__mro__",
        "__subclasses__",
        "gi_frame",
        "gi_code",
        "cr_frame",
        "cr_code",
        "f_globals",
        "f_locals",
        "f_builtins",
        "f_back",
        "mro",
        "format",
        "format_map",
    }
)

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
EMAIL_TRUSTED = EMAIL_ROOT / "tools/email/trusted"


def run(source: str) -> Outcome:
    return execute(
        source,
        source_name="plan.py",
        policy=build_policy(
            trusted_roots=[EMAIL_TRUSTED],
            import_roots=[EMAIL_ROOT],
        ),
        import_roots=[EMAIL_ROOT],
    )


def _wrap(body: str) -> str:
    return "def main():\n" + "".join(f"    {line}\n" for line in body.splitlines())


# --------------------------------------------------------------------------
# The issue #68 escape catalogue
# --------------------------------------------------------------------------


def test_type_mro_escape_has_no_name_to_resolve() -> None:
    # `type` is in FORBIDDEN_BUILTINS for runner.py; here it simply is not a
    # builtin the interpreter implements.
    with pytest.raises(NameResolutionError, match="'type'"):
        run(_wrap("obj = type(()).mro()[1]"))


def test_dunder_class_attribute_has_no_handler() -> None:
    with pytest.raises(UnsupportedNode, match="attribute access"):
        run(_wrap("x = (1).__class__"))


def test_generator_frame_globals_is_not_even_syntax_in_the_subset() -> None:
    # A generator expression has no handler at all -- the prover cannot model
    # one, so the interpreter cannot build one.
    with pytest.raises(UnsupportedNode, match="GeneratorExp"):
        run(_wrap("g = (x for x in [1])\ny = g.gi_frame.f_globals"))


def test_str_format_traversal_has_no_attribute_rule() -> None:
    with pytest.raises(UnsupportedNode, match="attribute access on str"):
        run(_wrap('y = "{0.__class__}".format(())'))


def test_help_is_not_a_builtin_here() -> None:
    with pytest.raises(NameResolutionError, match="'help'"):
        run(_wrap('help("os")'))


def test_dunder_import_is_not_a_name() -> None:
    with pytest.raises(NameResolutionError, match="__import__"):
        run(_wrap('m = __import__("os")'))


def test_builtins_mapping_is_not_a_name() -> None:
    with pytest.raises(NameResolutionError, match="__builtins__"):
        run(_wrap('e = __builtins__["eval"]'))


def test_eval_and_exec_are_not_builtins() -> None:
    for payload in ('eval("1")', 'exec("x = 1")', 'compile("1", "", "eval")'):
        with pytest.raises(NameResolutionError):
            run(_wrap(payload))


def test_getattr_indirection_is_not_available() -> None:
    # `operator.attrgetter("__globals__")` was the #68 bypass of the *string*
    # denylist. Neither `getattr` nor `operator` exists here.
    with pytest.raises(NameResolutionError, match="'getattr'"):
        run(_wrap('g = getattr((), "__class__")'))
    with pytest.raises(InterpError, match="allowlist"):
        run("import operator\n\ndef main():\n    return 1\n")


def test_open_is_not_a_builtin() -> None:
    with pytest.raises(NameResolutionError, match="'open'"):
        run(_wrap('f = open("/etc/passwd")'))


# --------------------------------------------------------------------------
# Constructs the denylist never had to consider, because CPython never saw them
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("while", "def main():\n    while True:\n        pass\n"),
        (
            "try",
            "def main():\n"
            "    try:\n"
            "        pass\n"
            "    except Exception:\n"
            "        pass\n",
        ),
        ("with", "def main():\n    with 1 as x:\n        pass\n"),
        ("yield", "def main():\n    yield 1\n"),
        ("async", "async def main():\n    return 1\n"),
        ("walrus", "def main():\n    if (n := 1):\n        return n\n"),
        ("global", "def main():\n    global x\n    x = 1\n"),
        ("del", "def main():\n    x = 1\n    del x\n"),
        ("nonlocal_class", "def main():\n    class C:\n        pass\n"),
        ("fstring", 'def main():\n    return f"{1}"\n'),
        ("starargs", "def f(*a):\n    return a\n\ndef main():\n    return f(1)\n"),
        ("match", "def main():\n    match 1:\n        case 1:\n            return 1\n"),
    ],
)
def test_out_of_subset_constructs_have_no_handler(label: str, source: str) -> None:
    """Not rejected by a rule -- unreachable for want of one."""
    with pytest.raises(InterpError):
        run(source)


# --------------------------------------------------------------------------
# The trusted-module surface
# --------------------------------------------------------------------------


TRUSTED_LAYER = '''\
"""A perfectly ordinary trusted layer that happens to import os."""

import os  # noqa: F401

import deal


@deal.has("write", "trusted")
def write_note(text: str) -> None:
    """Defined here, so this is the layer's interpreted surface."""
'''


@pytest.fixture
def layer_root(tmp_path: Path) -> Path:
    """A minimal trusted layer at ``<tmp>/tools/demo/trusted/effects.py``."""
    trusted = tmp_path / "tools/demo/trusted"
    trusted.mkdir(parents=True)
    # Ancestors outside the trusted root are inert namespace packages.
    (trusted / "__init__.py").write_text("")
    (trusted / "effects.py").write_text(TRUSTED_LAYER)
    return tmp_path


def _run_against_layer(root: Path, source: str) -> Outcome:
    saved = [k for k in sys.modules if k == "tools" or k.startswith("tools.")]
    kept = {k: sys.modules.pop(k) for k in saved}
    try:
        return execute(
            source,
            source_name="plan.py",
            policy=build_policy(
                trusted_roots=[root / "tools/demo/trusted"],
                import_roots=[root],
            ),
            import_roots=[root],
        )
    finally:
        for key in [k for k in sys.modules if k == "tools" or k.startswith("tools.")]:
            del sys.modules[key]
        sys.modules.update(kept)


def test_trusted_layer_imports_are_not_its_surface(layer_root: Path) -> None:
    """The escape that motivated ``exposed_names``.

    Python module attributes are transitive: ``import os`` inside ``effects.py``
    makes ``effects.os`` an attribute of the module. If a trusted layer's whole
    attribute table were exposed, ``effects.os.system(...)`` would be one access
    away -- through a trusted layer that did nothing wrong. A trusted layer
    exposes what it *defines*, never what it imports.
    """
    with pytest.raises(TrustedBoundaryError, match="not exposed"):
        _run_against_layer(
            layer_root,
            "from tools.demo.trusted import effects\n"
            "\n"
            "def main() -> None:\n"
            '    effects.os.system("id > /tmp/pwned")\n',
        )


def test_trusted_layer_still_exposes_what_it_defines(layer_root: Path) -> None:
    outcome = _run_against_layer(
        layer_root,
        "from tools.demo.trusted.effects import write_note\n"
        "\n"
        "def main() -> None:\n"
        '    write_note("hi")\n',
    )
    assert [effect[0] for effect in outcome.effects] == [
        "tools.demo.trusted.effects.write_note",
    ]


def test_allow_import_is_not_a_trust_grant() -> None:
    """``--allow-import`` grants importability, not trust.

    ``build_policy`` puts trusted-root-derived prefixes and dotted
    ``extra_allowed`` entries into the same ``allowed_prefixes`` tuple. Keying
    trust off that tuple would turn an operator's import opt-in into a full
    ``TrustedCallable`` surface for a module outside every trusted root, with no
    ``@deal.has`` markers -- contrary to AGENTS.md's trust-boundary rule. Trust
    is read from ``trusted_prefixes`` instead.
    """
    policy = build_policy(import_roots=[EMAIL_ROOT], extra_allowed=["tools.email"])
    assert policy.is_allowed("tools.email.trusted.effects")
    assert policy.trusted_prefixes == ()

    with pytest.raises(
        TrustedBoundaryError, match="outside authorized library origins"
    ):
        execute(
            "from tools.email.trusted.effects import send_email\n"
            "\n"
            "def main() -> None:\n"
            "    send_email('bob@example.com', 'hi')\n",
            source_name="plan.py",
            policy=policy,
            import_roots=[EMAIL_ROOT],
        )


RE_EXPORT_EFFECTS = """\
\"\"\"A trusted layer split across files, re-exporting a sibling's definition.\"\"\"

import deal

from tools.demo.trusted.schemas import Note


@deal.has("write", "trusted")
def write_note(note: str) -> None:
    \"\"\"Defined here.\"\"\"
"""

RE_EXPORT_SCHEMAS = """\
\"\"\"A sibling module under the same trusted root.\"\"\"


def Note(text: str) -> str:
    return text
"""


def test_trusted_layers_may_re_export_across_their_own_files(
    tmp_path: Path,
) -> None:
    """A layer is a trusted *root*, not a single file.

    Requiring ``__module__ == module.__name__`` dropped re-exports, so
    ``from tools.db.trusted.effects import UserRow`` -- a shape that ships in
    ``examples/email-from-db`` -- was refused although it proves. Widening to
    the trusted prefix is safe for the reason the layer is trusted at all; what
    stays excluded is anything defined outside every trusted root.
    """
    trusted = tmp_path / "tools/demo/trusted"
    trusted.mkdir(parents=True)
    # Ancestors outside the trusted root are inert namespace packages.
    (trusted / "__init__.py").write_text("")
    (trusted / "schemas.py").write_text(RE_EXPORT_SCHEMAS)
    (trusted / "effects.py").write_text(RE_EXPORT_EFFECTS)

    outcome = _run_against_layer(
        tmp_path,
        "from tools.demo.trusted.effects import Note, write_note\n"
        "\n"
        "def main() -> None:\n"
        "    write_note(Note('hi'))\n",
    )
    assert [effect[0] for effect in outcome.effects] == [
        "tools.demo.trusted.effects.Note",
        "tools.demo.trusted.effects.write_note",
    ]


def test_re_export_widening_still_excludes_outside_definitions(
    layer_root: Path,
) -> None:
    """``deal`` is imported by the layer, not defined under its trusted root."""
    with pytest.raises(TrustedBoundaryError, match="not exposed"):
        _run_against_layer(
            layer_root,
            "from tools.demo.trusted import effects\n"
            "\n"
            "def main():\n"
            "    return effects.deal\n",
        )


# --------------------------------------------------------------------------
# The denylists themselves
# --------------------------------------------------------------------------


def test_no_forbidden_builtin_is_reachable() -> None:
    """Every name the ``exec`` runtime had to remove is simply absent here."""
    from clauz3.interp.builtins import BUILTINS

    assert not (FORBIDDEN_BUILTINS & set(BUILTINS))


def test_no_forbidden_attribute_is_reachable() -> None:
    """Each denylisted attribute fails, and none of them is named in interp."""
    import clauz3.interp.interpreter as interpreter
    import clauz3.interp.methods as methods
    import clauz3.interp.values as values

    listed = set(FORBIDDEN_ATTRIBUTES)
    # ``methods`` is included because it is now the module that names attribute
    # strings; a denylisted name appearing in its table would be a real change.
    for module in (interpreter, methods, values):
        assert module.__file__ is not None
        source = Path(module.__file__).read_text()
        # `format`/`mro` appear in the runner denylist because CPython exposes
        # them; the interpreter never mentions any of these as a special case
        # (docstrings aside, which is why we check code lines only).
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        for attr in listed:
            assert f'"{attr}"' not in code, f"{module.__name__} special-cases {attr}"


ROW_SCHEMA_PROGRAM = "import clauz3\n\nclass R(clauz3.Row):\n    x: int\n\n"


@pytest.mark.parametrize("attr", sorted(FORBIDDEN_ATTRIBUTES))
def test_every_denylisted_attribute_is_unreachable(attr: str) -> None:
    with pytest.raises(InterpError):
        run(_wrap(f"x = (1).{attr}"))


@pytest.mark.parametrize("attr", [*sorted(FORBIDDEN_ATTRIBUTES), "x"])
def test_row_schema_attributes_are_unreachable(attr: str) -> None:
    """A ``Row`` *class* was the one attribute path without an allowlist.

    Probing an ``int`` (above) misses this: ``_expr_attribute`` has no rule for
    ints at all, so those tests pass for an unrelated reason. A ``RowClass``
    does have a branch, and it used to be a bare ``getattr(value.schema, ...)``
    -- resolving ``R.__mro__``, ``R.__class__`` (the metaclass) and
    ``R.__subclasses__`` into the value domain. Declared field names are probed
    too: class-level field access yields a live ``ColumnRef``, which only ever
    belongs inside a ``@clauz3.guarantee`` decorator, and those are never
    evaluated.
    """
    with pytest.raises(InterpError):
        run(ROW_SCHEMA_PROGRAM + _wrap(f"y = R.{attr}"))


def test_row_instances_still_expose_their_declared_fields() -> None:
    """Narrowing the class surface must not break reading a row."""
    outcome = run(
        ROW_SCHEMA_PROGRAM + "def main():\n    row = R(x=7)\n    return row.x\n"
    )
    assert outcome.value == 7
