"""Events performed by trusted calls, and contracts over them.

The point of an event is that a contract over it covers *every* tool that
performs it. Each prove test here pairs a tool that a per-function relation
would miss (``copy`` reading its source, ``remove`` as a kind of write) with a
contract over the event, and checks the guarantee now sees the call.
"""

from __future__ import annotations

import inspect
import textwrap
from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.differential_contracts import check_contract
from clauz3.events import PERFORMED, Event, _ParamRef, performs
from clauz3.prover import ProverConfigError, prove_text, validate_untrusted_source
from clauz3.spec import RelationError

EVENTS = """
from clauz3.events import Event


class FsEvent(Event):
    path: str


class Read(FsEvent): ...


class Write(FsEvent): ...


class Delete(Write): ...
"""

EFFECTS = """
import clauz3
from clauz3.events import performs
from {pkg}.events import Delete, Read, Write


@clauz3.trusted
@performs(lambda path: Read(path))
def read_file(path: str) -> str:
    return ""


@clauz3.trusted
@performs(lambda src, dest: [Read(src), Write(dest)])
def copy(src: str, dest: str, follow_symlinks: bool = True) -> None:
    pass


@clauz3.trusted
@performs(lambda path: Delete(path))
def remove(path: str) -> None:
    pass


@clauz3.trusted
def ping(host: str) -> None:
    pass
"""

CONTRACTS = """
from {pkg}.events import FsEvent, Read, Write

from clauz3.spec import ContractSpec, contract


@contract
def never_read_under(prefix: str) -> ContractSpec:
    return Read.all(lambda e: not e.path.startswith(prefix))


@contract
def read_only() -> ContractSpec:
    return Write.empty()


@contract
def touches_at_most(count: int) -> ContractSpec:
    return FsEvent.count() <= count
"""


def _prove(tmp_path: Path, body: str, guarantee: str, **files: str) -> Conclusion:
    pkg = tmp_path.name.replace("-", "_")
    package = tmp_path / pkg
    package.mkdir()
    (package / "__init__.py").write_text("")
    sources = {"events": EVENTS, "effects": EFFECTS, "contracts": CONTRACTS, **files}
    for name, source in sources.items():
        (package / f"{name}.py").write_text(textwrap.dedent(source.format(pkg=pkg)))
    program = f"""
import clauz3
from {pkg} import contracts
from {pkg}.effects import copy, ping, read_file, remove


@clauz3.guarantee(contracts.{guarantee})
def main() -> None:
{textwrap.indent(textwrap.dedent(body), "    ")}
"""
    results = prove_text(
        program,
        trusted_roots=[package],
        import_paths=[tmp_path],
        source_name="<program>",
    )
    assert len(results) == 1
    return results[0].proof.conclusion


@pytest.mark.parametrize(
    ("body", "guarantee", "expected"),
    [
        ('read_file("/srv/a")', 'never_read_under("/secret")', Conclusion.OK),
        ('read_file("/secret/a")', 'never_read_under("/secret")', Conclusion.FAIL),
        # The bypass a per-function relation misses: copy reads its source.
        ('copy("/secret/a", "/tmp/a")', 'never_read_under("/secret")', Conclusion.FAIL),
        ('copy("/srv/a", "/secret/a")', 'never_read_under("/secret")', Conclusion.OK),
        # Delete subclasses Write, so read_only forbids it.
        ('remove("/tmp/a")', "read_only()", Conclusion.FAIL),
        ('copy("/srv/a", "/tmp/a")', "read_only()", Conclusion.FAIL),
        ('read_file("/srv/a")\nping("example.org")', "read_only()", Conclusion.OK),
        # The base class covers every subclass: copy performs two events.
        ('copy("/a", "/b")\nremove("/c")', "touches_at_most(3)", Conclusion.OK),
        ('copy("/a", "/b")\nremove("/c")', "touches_at_most(2)", Conclusion.FAIL),
        # A guarded call performs its events only where it is reached.
        (
            'if len("x") > 5:\n    copy("/secret/a", "/tmp/a")',
            'never_read_under("/secret")',
            Conclusion.OK,
        ),
        (
            'for path in ["/srv/a", "/secret/b"]:\n    read_file(path)',
            'never_read_under("/secret")',
            Conclusion.FAIL,
        ),
    ],
)
def test_contracts_over_events(
    tmp_path: Path, body: str, guarantee: str, expected: Conclusion
) -> None:
    assert _prove(tmp_path, body, guarantee) == expected


def test_a_field_the_event_lacks_is_refused(tmp_path: Path) -> None:
    contracts = CONTRACTS + textwrap.dedent("""

        @contract
        def only_src(prefix: str) -> ContractSpec:
            return Read.all(lambda e: e.src.startswith(prefix))
    """)
    conclusion = _prove(
        tmp_path, 'read_file("/a")', 'only_src("/")', contracts=contracts
    )
    assert conclusion == Conclusion.SKIP


def test_performs_naming_a_missing_parameter_is_a_config_error(
    tmp_path: Path,
) -> None:
    effects = EFFECTS.replace(
        "lambda path: Delete(path)", "lambda target: Delete(target)"
    )
    with pytest.raises(ProverConfigError, match="not its parameters"):
        _prove(tmp_path, 'remove("/a")', "read_only()", effects=effects)


@pytest.mark.parametrize(
    "source",
    [
        "from clauz3.events import performs\n@performs(lambda: [])\n",
        "from clauz3.events import performs as p\n@p(lambda: [])\n",
        "import clauz3.events as ev\n@ev.performs(lambda: [])\n",
    ],
)
def test_performs_is_refused_in_agent_source(source: str) -> None:
    with pytest.raises(ProverConfigError):
        validate_untrusted_source(
            source=source + "def f() -> None:\n    pass\n", path=Path("plan.py")
        )


class Sent(Event):
    to: str


def test_performs_rejects_a_computed_field() -> None:
    with pytest.raises(RelationError, match="computed value"):
        performs(lambda to: Sent("bob@example.org"))


def test_performs_rejects_a_non_event() -> None:
    with pytest.raises(RelationError, match="Event instances"):
        performs(lambda to: to)


def test_reserved_field_names_are_refused() -> None:
    with pytest.raises(RelationError, match="reserved"):

        class Counted(Event):
            count: int  # type: ignore[assignment]


def test_events_are_registered_by_qualified_name() -> None:
    @performs(lambda addr: Sent(addr))
    def send(addr: str, body: str) -> None: ...

    key = f"{send.__module__}.{send.__qualname__}"
    try:
        assert PERFORMED[key] == (Sent(_ParamRef("addr")),)  # type: ignore[arg-type]
    finally:
        del PERFORMED[key]


def test_the_concrete_oracle_derives_events() -> None:
    def send(addr: str, body: str = "") -> None: ...

    performs(lambda addr: Sent(addr))(send)
    qualified = f"{send.__module__}.{send.__qualname__}"
    signatures = {qualified: inspect.signature(send)}
    spec = Sent.all(lambda e: e.to == "bob")
    try:
        assert check_contract(spec, [(qualified, ("bob",), {})], signatures)
        assert not check_contract(spec, [(qualified, ("eve",), {})], signatures)
        assert check_contract(Sent.empty(), [], signatures)
        assert not check_contract(Sent.empty(), [(qualified, ("bob",), {})], signatures)
    finally:
        del PERFORMED[qualified]


def test_an_event_class_imported_twice_still_matches() -> None:
    """Switching layers can import an events module twice; both copies match."""

    def send(addr: str) -> None: ...

    copy = type(
        "Sent",
        (Event,),
        {
            "__annotations__": {"to": str},
            "__module__": Sent.__module__,
            "__qualname__": Sent.__qualname__,
        },
    )
    assert copy is not Sent
    performs(lambda addr: copy(addr))(send)
    qualified = f"{send.__module__}.{send.__qualname__}"
    signatures = {qualified: inspect.signature(send)}
    try:
        assert not check_contract(
            Sent.all(lambda e: e.to == "bob"), [(qualified, ("eve",), {})], signatures
        )
    finally:
        del PERFORMED[qualified]
