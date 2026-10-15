"""Changing projects may refresh genuine namespaces, never poisoned snapshots."""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest

from clauz3.approvals.policy import _entails_all
from clauz3.interp.errors import TrustedBoundaryError
from clauz3.prover import (
    _load_trusted_modules,
    check_condition_signatures,
    prove_path,
)


@pytest.fixture
def namespace_name() -> Iterator[str]:
    name = f"refresh_namespace_{uuid4().hex}"
    yield name
    for cached in tuple(sys.modules):
        if cached == name or cached.startswith(name + "."):
            sys.modules.pop(cached, None)


def _project(root: Path, namespace: str, domain: str) -> tuple[Path, Path]:
    layer = root / namespace / domain / "trusted"
    layer.mkdir(parents=True)
    (layer / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (layer / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def only_good() -> ContractSpec:\n"
        "    return effect('record').where(lambda e: e.value != 'good').empty()\n"
    )
    entry = root / "entry.py"
    entry.write_text(
        "import clauz3\n"
        f"from {namespace}.{domain}.trusted import effects\n"
        f"from {namespace}.{domain}.trusted.effects import record\n"
        f"from {namespace}.{domain}.trusted.contracts import only_good\n"
        "@clauz3.guarantee(only_good())\n"
        "def main() -> None:\n"
        "    record('good')\n"
    )
    return layer, entry


@pytest.mark.parametrize("consumer", ["prove", "approval", "empty-approval"])
@pytest.mark.parametrize("inspect_between_requests", [False, True])
def test_native_projects_switch_a_b_a_without_orphaning_cached_children(
    tmp_path: Path,
    namespace_name: str,
    monkeypatch: pytest.MonkeyPatch,
    consumer: str,
    inspect_between_requests: bool,
) -> None:
    projects = [
        _project(tmp_path / "a", namespace_name, "email"),
        _project(tmp_path / "b", namespace_name, "gh"),
    ]
    retained: dict[int, dict[str, ModuleType]] = {}
    ambient_module = f"{namespace_name}.email.trusted"
    parked_module = f"{namespace_name}.gh.trusted"
    for index in (0, 1, 0, 1, 0):
        layer, entry = projects[index]
        domain = "email" if index == 0 else "gh"
        module = f"{namespace_name}.{domain}.trusted"
        checked: list[ModuleType] = []
        active: dict[str, ModuleType] = {}

        def check_selected(
            selected: ModuleType,
            *,
            prefix: str = module,
            selected_root: Path = layer,
            project: Path = entry.parent,
            observed: list[ModuleType] = checked,
            selected_index: int = index,
            selected_modules: dict[str, ModuleType] = active,
        ) -> None:
            if selected.__name__.startswith(prefix + "."):
                assert (
                    Path(vars(selected)["__file__"])
                    .resolve()
                    .is_relative_to(selected_root.resolve())
                )
                active_path = vars(vars(sys.modules[namespace_name])["__path__"])
                assert active_path["_path"] == [
                    str((project / namespace_name).resolve())
                ]
                for name in (
                    namespace_name,
                    prefix.rpartition(".")[0],
                    prefix,
                    selected.__name__,
                ):
                    current = sys.modules[name]
                    assert selected_modules.setdefault(name, current) is current
                    if selected_index in retained:
                        assert retained[selected_index][name] is current
                observed.append(selected)
            check_condition_signatures(selected)

        monkeypatch.setattr("clauz3.prover.check_condition_signatures", check_selected)
        if consumer == "prove":
            proofs = prove_path(
                entry, trusted_roots=[layer], import_roots=[entry.parent]
            )
            assert [proof.proof.conclusion.name for proof in proofs] == ["OK"]
        else:
            assert (
                _entails_all(
                    program=entry.read_text(),
                    clauses=("policy.only_good()",) if consumer == "approval" else (),
                    imports=(("policy", module + ".contracts"),),
                    target="main",
                    trusted_roots=[layer],
                    import_roots=[entry.parent],
                )
                is True
            )

        assert checked, "the selected layer must be inspected during the operation"
        retained.setdefault(index, active)
        for name, cached in retained[0].items():
            assert sys.modules[name] is cached
        # B's children stay with B's package objects while ambient A returns.
        # Their retained references are checked again when B resumes above.
        assert "gh" not in vars(sys.modules[namespace_name])
        assert not any(
            name == f"{namespace_name}.gh" or name.startswith(parked_module)
            for name in sys.modules
        )
        effects = retained[index][module + ".effects"]
        assert vars(retained[index][module])["effects"] is effects
        assert vars(effects)["EVENTS"] == []
        path_state = vars(vars(sys.modules[namespace_name])["__path__"])
        # B is selected inside its operation, as checked above. Once it exits,
        # the previously resident A namespace and its path state are restored.
        ambient_root = projects[0][1].parent
        assert path_state["_path"] == [str((ambient_root / namespace_name).resolve())]
        # The positive result is not just a vacuous module-import success.
        if consumer != "empty-approval":
            bad = entry.read_text().replace("record('good')", "record('bad')")
            if consumer == "prove":
                bad_entry = entry.with_name("bad.py")
                bad_entry.write_text(bad)
                proofs = prove_path(
                    bad_entry, trusted_roots=[layer], import_roots=[entry.parent]
                )
                assert [proof.proof.conclusion.name for proof in proofs] == ["FAIL"]
            else:
                assert (
                    _entails_all(
                        program=bad,
                        clauses=("policy.only_good()",),
                        imports=(("policy", module + ".contracts"),),
                        target="main",
                        trusted_roots=[layer],
                        import_roots=[entry.parent],
                    )
                    is False
                )
        if inspect_between_requests:
            # Normal introspection outside the temporary import roots updates
            # _last_parent_path while Python retains the previous _path.
            assert tuple(vars(sys.modules[namespace_name])["__path__"])
            assert tuple(vars(sys.modules[ambient_module])["__path__"])


@pytest.mark.parametrize("switch_project", [False, True])
@pytest.mark.parametrize(
    "poison",
    ["callback", "_recalculate", "_get_parent_path", "snapshot", "spec"],
    ids=[
        "finder-callback",
        "extra-instance-key-recalculate",
        "extra-instance-key-get-parent-path",
        "forged-path-snapshot",
        "path-spec-identity",
    ],
)
def test_poisoned_namespace_is_refused_before_any_initializer_or_hook(
    tmp_path: Path,
    namespace_name: str,
    monkeypatch: pytest.MonkeyPatch,
    switch_project: bool,
    poison: str,
) -> None:
    old_root, new_root = tmp_path / "a", tmp_path / "b"
    (old_root / namespace_name).mkdir(parents=True)
    selected_root = new_root if switch_project else old_root
    layer = selected_root / namespace_name / "trusted"
    layer.mkdir(parents=True)
    (layer / "effects.py").write_text(
        "raise AssertionError('initializer must not execute')\n"
    )
    original_path = sys.path[:]
    monkeypatch.setattr(sys, "path", [str(old_root), *original_path])
    namespace = importlib.import_module(namespace_name)
    namespace_path = vars(namespace)["__path__"]
    state: dict[str, Any] = vars(namespace_path)
    accesses: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> list[str]:
        accesses.append("hook")
        raise AssertionError("namespace hook must not execute")

    if poison == "callback":
        state["_path_finder"] = forbidden
    elif poison in {"_recalculate", "_get_parent_path"}:
        state[poison] = forbidden
    elif poison == "snapshot":
        state["_path"] = [str(tmp_path / "forged")]
    else:
        vars(vars(namespace)["__spec__"])["submodule_search_locations"] = list(
            state["_path"]
        )
    monkeypatch.setattr(sys, "path", [str(selected_root), *original_path])

    with pytest.raises(TrustedBoundaryError, match="namespace"):
        _load_trusted_modules([layer], import_paths=[selected_root])

    assert accesses == []
    assert namespace_name + ".trusted.effects" not in sys.modules
    assert sys.modules[namespace_name] is namespace
