"""The shipped database layers share names but not schemas or contract models."""

import importlib
import sys
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import astroid
import pytest

from clauz3 import spec
from clauz3._vendor.deal_solver import Conclusion, Proof
from clauz3._vendor.deal_solver._funcs import FUNCTIONS
from clauz3.prover import AgentTheorem, prove_path, prove_text

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples/email-from-db"
STDLIB = REPO / "src/clauz3/stdlib/db"
MODULE = "tools.db.trusted"
MODEL = MODULE + ".contracts.only_where"


@pytest.mark.parametrize("first", ["a", "b"])
@pytest.mark.parametrize("evict", [False, True], ids=["continuous", "evicted"])
def test_new_children_stay_with_their_selected_package_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first: str, evict: bool
) -> None:
    name = f"parked_children_{uuid4().hex}"
    roots: dict[str, Path] = {}
    for label in ("a", "b"):
        root = tmp_path / label / name
        child = root / f"only_{label}"
        child.mkdir(parents=True)
        (root / "effects.py").write_text(
            "import clauz3\n"
            "@clauz3.trusted\n"
            "def record(value: str) -> None:\n"
            "    raise AssertionError('proof must not execute record')\n"
        )
        (child / "contracts.py").write_text(
            "from clauz3.spec import ContractSpec, contract, effect\n"
            "@contract\n"
            "def budget() -> ContractSpec:\n"
            f"    return effect('record').count() <= {1 if label == 'a' else 0}\n"
        )
        roots[label] = root

    original_prove = AgentTheorem.prove
    modules: dict[str, dict[str, ModuleType]] = {}
    models: dict[str, object] = {}

    def observe(theorem: AgentTheorem) -> Proof:
        names = (
            name,
            f"{name}.effects",
            f"{name}.only_{project}",
            f"{name}.only_{project}.contracts",
        )
        active = {key: sys.modules[key] for key in names}
        parent = active[name]
        child = active[f"{name}.only_{project}"]
        contracts = active[f"{name}.only_{project}.contracts"]
        assert vars(parent)[f"only_{project}"] is child
        assert vars(child)["contracts"] is contracts
        assert f"only_{other}" not in vars(parent)
        assert f"{name}.only_{other}" not in sys.modules
        model = FUNCTIONS[f"{name}.only_{project}.contracts.budget"]
        if project in modules:
            assert all(active[key] is value for key, value in modules[project].items())
            assert model is models[project]
        else:
            modules[project] = active
            models[project] = model
        return original_prove(theorem)

    monkeypatch.setattr(AgentTheorem, "prove", observe)
    second = "b" if first == "a" else "a"
    ambient: dict[str, ModuleType] = {}
    for index, project in enumerate((first, second, first, second)):
        other = "b" if project == "a" else "a"
        if evict and index == 2:
            # Embedders may remove imports between requests, as the suite's
            # cleanup does. Parked image identities still have to resume.
            for key in list(sys.modules):
                if key == name or key.startswith(name + "."):
                    del sys.modules[key]
        results = prove_text(
            "import clauz3\n"
            f"from {name}.effects import record\n"
            f"from {name}.only_{project}.contracts import budget\n"
            "@clauz3.guarantee(budget())\n"
            "def main() -> None:\n"
            "    record('one')\n",
            trusted_roots=[roots[project]],
            import_paths=[roots[project].parent],
        )
        expected = Conclusion.OK if project == "a" else Conclusion.FAIL
        assert [result.proof.conclusion for result in results] == [expected]
        if not ambient:
            ambient = dict(modules[first])
        assert all(sys.modules[key] is value for key, value in ambient.items())
        assert f"only_{second}" not in vars(ambient[name])
        assert f"{name}.only_{second}" not in sys.modules
        assert f"{name}.only_{second}.contracts" not in sys.modules
        assert f"{name}.only_{second}.contracts.budget" not in FUNCTIONS
        assert f"{name}.only_{second}.contracts.budget" not in spec.REGISTERED_CONTRACTS
        assert FUNCTIONS[f"{name}.only_{first}.contracts.budget"] is models[first]


@pytest.mark.parametrize(
    "first", [EXAMPLE, STDLIB], ids=["example-first", "stdlib-first"]
)
def test_real_database_layers_switch_without_crossing_sources_or_schemas(
    first: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    second = STDLIB if first == EXAMPLE else EXAMPLE
    original_prove = AgentTheorem.prove
    modules: dict[Path, dict[str, ModuleType]] = {}
    asts: dict[Path, dict[str, astroid.Module]] = {}
    models: dict[Path, object] = {}
    checked: list[Path] = []

    def observe_proof(theorem: AgentTheorem) -> Proof:
        selected = project / "tools/db/trusted"
        active = {
            name: sys.modules[MODULE + "." + name] for name in ("effects", "contracts")
        }
        for name, module in active.items():
            assert (
                Path(vars(module)["__file__"]).resolve()
                == (selected / f"{name}.py").resolve()
            )
        if project in modules:
            assert all(
                active[name] is module for name, module in modules[project].items()
            )
        else:
            modules[project] = active

        effects, contracts = vars(active["effects"]), vars(active["contracts"])
        if project == EXAMPLE:
            assert "db_query" in effects and "read_rows" not in effects
            schema = sys.modules[MODULE + ".schemas"].UserRow
            assert tuple(schema.__annotations__) == (
                "name",
                "email",
                "consented",
                "role",
            )
            relation = contracts["DB"]
            assert relation.names == ("db_query",)
            assert relation.fields == frozenset({"table", "where"})
        else:
            assert "read_rows" in effects and "db_query" not in effects
            relation = contracts["DbRead"]
            assert relation.names == ("read_rows",)
            assert relation.fields == frozenset({"schema", "path", "table", "where"})
        assert relation.origin == str((selected / "contracts.py").resolve())
        assert relation in spec._RELATIONS[relation.origin]
        model = FUNCTIONS[MODEL]
        if project in models:
            assert model is models[project]
        else:
            models[project] = model

        result = original_prove(theorem)
        source_nodes = {
            name: astroid.MANAGER.ast_from_module_name(MODULE + "." + name)
            for name in ("effects", "contracts")
        }
        for name, node in source_nodes.items():
            assert Path(node.file).resolve() == (selected / f"{name}.py").resolve()
        if project in asts:
            assert all(
                source_nodes[name] is node for name, node in asts[project].items()
            )
        else:
            asts[project] = source_nodes
        checked.append(project)
        return result

    monkeypatch.setattr(AgentTheorem, "prove", observe_proof)
    ambient: dict[str, ModuleType] = {}
    for project in (first, second, first):
        cases = (
            ("cases/newsletter_pass.py", "cases/literal_address_fail.py")
            if project == EXAMPLE
            else ("tests/cases/only_where_pass.py", "tests/cases/only_where_fail.py")
        )
        layer = project / "tools/db/trusted"
        for relative, expected in zip(
            cases, (Conclusion.OK, Conclusion.FAIL), strict=True
        ):
            results = prove_path(
                project / relative, trusted_roots=[layer], import_roots=[project]
            )
            assert [result.proof.conclusion for result in results] == [expected]
            if not ambient:
                ambient = {
                    name: sys.modules[name]
                    for name in (
                        "tools",
                        "tools.db",
                        MODULE,
                        MODULE + ".effects",
                        MODULE + ".contracts",
                    )
                }
            assert all(sys.modules[name] is module for name, module in ambient.items())
            assert vars(ambient[MODULE])["effects"] is ambient[MODULE + ".effects"]
            assert vars(ambient[MODULE])["contracts"] is ambient[MODULE + ".contracts"]
            assert FUNCTIONS[MODEL] is models[first]
    assert checked == [first, first, second, second, first, first]
    assert models[first] is not models[second]
    assert modules[first]["effects"] is not modules[second]["effects"]


def test_unrelated_host_module_does_not_become_layer_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = f"unrelated_host_{uuid4().hex}"
    missing = f"unavailable_optional_{uuid4().hex}"
    (tmp_path / f"{name}.py").write_text(
        "try:\n"
        "    from math import sqrt as optional\n"
        "except ImportError:\n"
        f"    from {missing} import optional\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    host = importlib.import_module(name)
    assert host.optional(4) == 2
    assert missing not in sys.modules
    bank = REPO / "examples/bank"
    results = prove_path(
        bank / "cases/max_spend_pass.py",
        trusted_roots=[bank / "tools/bank/trusted"],
        # Both the working directory and an embedder's application directory
        # can be search roots without every resident host module being a layer.
        import_roots=[bank, REPO, tmp_path],
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]
    assert sys.modules[name] is host
    assert host.optional(4) == 2
    assert missing not in sys.modules
