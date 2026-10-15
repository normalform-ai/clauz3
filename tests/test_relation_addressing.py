"""How a contract addresses an effect, and why an unresolvable name is refused.

A relation that matches no facts is not a safe failure. ``AllSpec`` folds one
clause per matched fact with ``and_expr``, and a conjunction of no clauses is
``True``, so ``Read.all(...)`` over an empty relation holds however the program
behaves. Every test here turns on that: the program *does* call the effect, so
a guarantee that stops constraining it is a false ``proved!``.

The specific regression is the marker migration. A layer written before
effects were addressed by name says ``effect("db_read")`` against
``@deal.has("read", "db_read")`` on a function called ``read_rows``. When
markers stopped being addressable, that relation silently went empty, and
``only_read_under("/srv")`` proved for a program reading ``/etc/shadow``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from clauz3 import spec as spec_module
from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProverConfigError, prove_text
from clauz3.spec import RelationError, effect

LEGACY_EFFECTS = """
import deal


@deal.has("read", "db_read", "trusted")
def read_rows(path: str, table: str) -> str:
    return ""
"""

NATIVE_EFFECTS = """
import clauz3


@clauz3.trusted
def read_rows(path: str, table: str) -> str:
    return ""


@clauz3.trusted
def write_row(path: str, row: str) -> None:
    pass
"""

PROGRAM = """
import clauz3
from {pkg} import contracts
from {pkg}.effects import read_rows


@clauz3.guarantee(contracts.only_read_under("/srv"))
def main() -> None:
    read_rows("/etc/shadow", "users")
"""


def _layer(tmp_path: Path, *, effects: str, contracts: str) -> tuple[str, Path]:
    pkg = tmp_path.name.replace("-", "_")
    package = tmp_path / pkg
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(textwrap.dedent(effects))
    (package / "contracts.py").write_text(textwrap.dedent(contracts))
    return pkg, package


def _prove(tmp_path: Path, *, effects: str, contracts: str) -> Conclusion:
    pkg, package = _layer(tmp_path, effects=effects, contracts=contracts)
    return _prove_layer(tmp_path, pkg, package)[0]


def _prove_layer(tmp_path: Path, pkg: str, package: Path) -> tuple[Conclusion, str]:
    results = prove_text(
        PROGRAM.format(pkg=pkg),
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="<program>",
    )
    assert len(results) == 1
    return results[0].proof.conclusion, str(results[0].proof.error)


# ── an unresolvable relation is a configuration error, not a proof ───────────


def test_a_marker_named_relation_is_refused(tmp_path: Path) -> None:
    """The regression: ``effect("db_read")`` against a layer that has no such effect.

    Before this check the relation matched nothing and the program below --
    which reads ``/etc/shadow`` under a ``/srv`` guarantee -- reported
    ``proved!``.
    """

    with pytest.raises(ProverConfigError, match="not a trusted effect"):
        _prove(
            tmp_path,
            effects=LEGACY_EFFECTS,
            contracts="""
                from clauz3.spec import ContractSpec, contract, effect

                DbRead = effect("db_read")      # a @deal.has marker, not a function


                @contract
                def only_read_under(root: str) -> ContractSpec:
                    return DbRead.all(lambda e: e.path.startswith(root))
            """,
        )


def test_the_error_names_the_effects_that_do_exist(tmp_path: Path) -> None:
    with pytest.raises(ProverConfigError, match=r"Known effects: \['read_rows'\]"):
        _prove(
            tmp_path,
            effects=LEGACY_EFFECTS,
            contracts="""
                from clauz3.spec import ContractSpec, contract, effect

                DbRead = effect("db_read")


                @contract
                def only_read_under(root: str) -> ContractSpec:
                    return DbRead.all(lambda e: e.path.startswith(root))
            """,
        )


def test_a_typo_in_a_relation_name_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ProverConfigError, match="'read_rowz'"):
        _prove(
            tmp_path,
            effects=NATIVE_EFFECTS,
            contracts="""
                from clauz3.spec import ContractSpec, contract, effect

                DbRead = effect("read_rowz")


                @contract
                def only_read_under(root: str) -> ContractSpec:
                    return DbRead.all(lambda e: e.path.startswith(root))
            """,
        )


def test_a_relation_built_while_solving_is_refused_too(tmp_path: Path) -> None:
    """A relation built inside a contract helper never passes the import check.

    It is checked where it is used instead, so the proof ends as a refusal
    rather than an empty relation holding vacuously.
    """

    conclusion = _prove(
        tmp_path,
        effects=NATIVE_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract, effect


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return effect("db_read").all(lambda e: e.path.startswith(root))
        """,
    )
    assert conclusion == Conclusion.SKIP


def test_the_same_layer_proves_when_the_relation_resolves(tmp_path: Path) -> None:
    """The control: name the function and the guarantee bites again."""

    conclusion = _prove(
        tmp_path,
        effects=NATIVE_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract, effect

            DbRead = effect("read_rows")


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return DbRead.all(lambda e: e.path.startswith(root))
        """,
    )
    assert conclusion == Conclusion.FAIL


def test_a_cached_contract_module_is_re_checked(tmp_path: Path) -> None:
    """Import caching must not launder a bad relation into a later proof.

    Python imports a contract module once per process, so the second proof
    never re-runs its ``effect(...)`` calls. The relation is re-checked by
    source file, which also keeps two layers that share a module name apart.
    """

    pkg, package = _layer(
        tmp_path,
        effects=LEGACY_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract, effect

            DbRead = effect("db_read")


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return DbRead.all(lambda e: e.path.startswith(root))
        """,
    )
    for attempt in ("first import", "cached import"):
        with pytest.raises(ProverConfigError, match="not a trusted effect"):
            _prove_layer(tmp_path, pkg, package)
        assert attempt  # both proofs refuse, not just the one that imported


# ── naming the function itself ───────────────────────────────────────────────


def test_a_relation_may_name_the_function(tmp_path: Path) -> None:
    conclusion = _prove(
        tmp_path,
        effects=NATIVE_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract, effect

            from {pkg}.effects import read_rows

            DbRead = effect(read_rows)


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return DbRead.all(lambda e: e.path.startswith(root))
        """.replace("{pkg}", tmp_path.name.replace("-", "_")),
    )
    assert conclusion == Conclusion.FAIL


def test_naming_a_function_that_is_not_an_effect_is_refused(tmp_path: Path) -> None:
    """An ordinary helper is not a boundary, so no fact would ever match it."""

    with pytest.raises(ProverConfigError, match="not a trusted effect"):
        _prove(
            tmp_path,
            effects=NATIVE_EFFECTS
            + "\n\ndef helper(path: str) -> str:\n    return path\n",
            contracts="""
                from clauz3.spec import ContractSpec, contract, effect

                from {pkg}.effects import helper

                DbRead = effect(helper)


                @contract
                def only_read_under(root: str) -> ContractSpec:
                    return DbRead.all(lambda e: e.path.startswith(root))
            """.replace("{pkg}", tmp_path.name.replace("-", "_")),
        )


# ── fields are checked against the signature, where the relation is built ────


def test_a_field_the_effect_does_not_have_is_refused(tmp_path: Path) -> None:
    """``read_rows(path, table)`` has no ``paht``.

    Previously this surfaced only at solve time, and only if some fact
    matched: with an unresolvable relation *and* a bad field, nothing was
    checked at all.
    """

    pkg, package = _layer(
        tmp_path,
        effects=NATIVE_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract, effect

            DbRead = effect("read_rows")


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return DbRead.all(lambda e: e.paht.startswith(root))
        """,
    )
    conclusion, error = _prove_layer(tmp_path, pkg, package)
    assert conclusion == Conclusion.SKIP
    assert "'paht'" in error
    assert "'path', 'table'" in error, "the message lists what is available"


def test_a_field_missing_from_one_covered_effect_is_refused(tmp_path: Path) -> None:
    """A multi-effect relation offers only the fields they share.

    ``read_rows(path, table)`` and ``write_row(path, row)`` share ``path``;
    ``table`` belongs to one of them, so a predicate reading it would meet a
    fact without it.
    """

    pkg, package = _layer(
        tmp_path,
        effects=NATIVE_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract, effect

            Both = effect("read_rows", "write_row")


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return Both.all(lambda e: e.table == root)
        """,
    )
    conclusion, error = _prove_layer(tmp_path, pkg, package)
    assert conclusion == Conclusion.SKIP
    assert "'table'" in error
    assert "['path']" in error, "only the shared field is available"


def test_a_shared_field_is_accepted(tmp_path: Path) -> None:
    conclusion = _prove(
        tmp_path,
        effects=NATIVE_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract, effect

            Both = effect("read_rows", "write_row")


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return Both.all(lambda e: e.path.startswith(root))
        """,
    )
    assert conclusion == Conclusion.FAIL


def test_fields_are_unknown_for_a_relation_over_every_effect() -> None:
    """``effect()`` covers whatever a program calls, so no field is guaranteed.

    The solve-time check in the expression compiler remains the backstop for
    these.
    """

    relation = effect()
    assert relation.fields is None
    relation.all(lambda e: e.anything == 1)  # accepted here, checked on use


def test_a_relation_built_outside_a_proof_checks_fields_from_the_callable() -> None:
    """No proof in progress, but a function target still carries its signature."""

    def send_email(addr: str, msg: str) -> None: ...

    relation = effect(send_email)
    assert relation.fields == frozenset({"addr", "msg"})
    with pytest.raises(RelationError, match="'addres'"):
        relation.all(lambda e: e.addres == "bob")


def test_a_relation_imported_from_outside_the_roots_is_checked_on_use(
    tmp_path: Path,
) -> None:
    """Neither check at load time sees it, so the one on use is what refuses it.

    A contract module may import a relation from a layer that is not part of
    this proof --- ``stdlib:grep`` imports ``stdlib:filesystem``'s contracts
    for their path rule --- so a relation built outside the selected roots is
    deliberately not checked as it is built. It is still an empty relation if
    it names nothing, and this program reads ``/etc/shadow`` under a ``/srv``
    guarantee, so without the check on use it reported ``proved!``.
    """

    pkg, package = _layer(
        tmp_path,
        effects=NATIVE_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract

            from outside_layer import DbRead


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return DbRead.all(lambda e: e.path.startswith(root))
        """,
    )
    (tmp_path / "outside_layer.py").write_text(
        "from clauz3.spec import effect\n\nDbRead = effect('db_read')\n"
    )
    conclusion, error = _prove_layer(tmp_path, pkg, package)
    assert conclusion == Conclusion.SKIP
    assert "not a trusted effect" in error


def test_a_relation_imported_from_outside_the_roots_still_proves(
    tmp_path: Path,
) -> None:
    """The control: the same import resolves, so the guarantee still bites."""

    pkg, package = _layer(
        tmp_path,
        effects=NATIVE_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract

            from outside_resolving import DbRead


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return DbRead.all(lambda e: e.path.startswith(root))
        """,
    )
    (tmp_path / "outside_resolving.py").write_text(
        "from clauz3.spec import effect\n\nDbRead = effect('read_rows')\n"
    )
    conclusion, _ = _prove_layer(tmp_path, pkg, package)
    assert conclusion == Conclusion.FAIL


# ── one name, one effect ─────────────────────────────────────────────────────


def test_two_effects_with_one_name_are_refused(tmp_path: Path) -> None:
    """Addressing is by name, so a name meaning two functions means nothing.

    ``load_trusted_effects`` keyed a dict on the name alone, so proving two
    layers that share one — installing ``stdlib:editor`` beside a layer of your
    own with its own ``edit_file`` — kept whichever file was read last. A
    relation naming it then offered the *other* module's parameters, and the
    failure surfaced as a puzzling field error rather than the collision.
    """

    pkg, package = _layer(
        tmp_path,
        effects=NATIVE_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract, effect

            DbRead = effect("read_rows")


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return DbRead.all(lambda e: e.path.startswith(root))
        """,
    )
    other = tmp_path / "other"
    other.mkdir()
    (other / "__init__.py").write_text("")
    (other / "effects.py").write_text(
        textwrap.dedent(
            """
            import clauz3


            @clauz3.trusted
            def read_rows(handle: int) -> str:
                return ""
            """
        )
    )

    with pytest.raises(ProverConfigError, match="two trusted effects are called"):
        prove_text(
            PROGRAM.format(pkg=pkg),
            trusted_roots=[package, other],
            import_paths=[tmp_path],
            source_name="<program>",
        )


@pytest.mark.parametrize("repeat", ["the same root twice", "a root and its parent"])
def test_one_layer_reached_twice_is_not_a_collision(
    tmp_path: Path, repeat: str
) -> None:
    """Roots repeat and nest, and a second visit to one file is not two effects.

    ``--trusted-root tools --trusted-root tools/email/trusted`` is a reasonable
    thing to write; refusing it named the same ``effects.py`` on both sides of
    the collision and asked the author to rename one of them.
    """

    pkg, package = _layer(
        tmp_path,
        effects=NATIVE_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract, effect

            DbRead = effect("read_rows")


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return DbRead.all(lambda e: e.path.startswith(root))
        """,
    )
    roots = (
        [package, package] if repeat == "the same root twice" else [tmp_path, package]
    )
    results = prove_text(
        PROGRAM.format(pkg=pkg),
        trusted_roots=roots,
        import_paths=[tmp_path],
        source_name="<program>",
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]


def test_the_same_name_in_one_layer_alone_is_fine(tmp_path: Path) -> None:
    """The control: the collision is between roots, not within one."""

    conclusion = _prove(
        tmp_path,
        effects=NATIVE_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract, effect

            DbRead = effect("read_rows")


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return DbRead.all(lambda e: e.path.startswith(root))
        """,
    )
    assert conclusion == Conclusion.FAIL


def test_a_relation_rebuilt_while_solving_is_registered_once(tmp_path: Path) -> None:
    """A helper that builds its relation on each call must not leak one each.

    ``effect()`` runs once per contract *module* only when the relation is
    module-level; inside a helper it runs once per proof, and the registry a
    later proof re-checks is process-global.
    """

    pkg, package = _layer(
        tmp_path,
        effects=NATIVE_EFFECTS,
        contracts="""
            from clauz3.spec import ContractSpec, contract, effect


            @contract
            def only_read_under(root: str) -> ContractSpec:
                return effect("read_rows").all(lambda e: e.path.startswith(root))
        """,
    )
    for _ in range(3):
        assert _prove_layer(tmp_path, pkg, package)[0] == Conclusion.FAIL

    origin = str((package / "contracts.py").resolve())
    assert len(spec_module._RELATIONS[origin]) == 1


def test_a_relation_over_a_bare_string_is_refused() -> None:
    """``EffectRelation("send_email")`` would cover ten one-letter effects.

    ``effect()`` always passes a tuple, but the class is reachable from a
    trusted layer --- ``stdlib:filesystem`` exports builders that take one ---
    and a string is iterable, so the mistake would be a relation matching
    nothing rather than a type error.
    """

    with pytest.raises(RelationError, match="must be a tuple"):
        spec_module.EffectRelation(names="send_email")  # type: ignore[arg-type]
