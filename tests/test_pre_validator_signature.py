"""A condition predicate must declare the parameters deal will pass it (#90).

``@clauz3.requires`` delegates to ``deal.pre``, which runs an explicit predicate
as ``predicate(*args, **kwargs)`` with the caller's raw arguments; ``ensures``
runs its predicate through ``deal.ensure`` as ``predicate(*args, result=...,
**kwargs)``, or through ``deal.post`` as ``predicate(result)``. A predicate whose
parameter list differs from what deal passes raises ``TypeError`` on calls the
function itself accepts. The prover never looked at predicate parameters, so
such a trusted layer proved clean and crashed at runtime. The trusted-layer
load now checks non-class callables bound directly in module globals, including
package ``__init__.py`` modules. It does not traverse classes or containers.
"""

from __future__ import annotations

import functools
import importlib
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import deal
import pytest

import clauz3
from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import (
    ProverConfigError,
    check_condition_signatures,
    prove_path,
    prove_text,
)
from clauz3.runner import RunConfigError, run_source

FIXTURES = Path(__file__).resolve().parent / "fixtures/pre_signature"
FIXTURE_TRUSTED = FIXTURES / "sigtrusted"
PACKAGE_TRUSTED = FIXTURES / "pkgtrusted"

PRE_MISMATCH = r"precondition predicate .* does not match the function signature"
POST_MISMATCH = r"postcondition predicate .* (does not match|must take exactly one)"


def test_mismatched_predicate_fails_the_trusted_load() -> None:
    """The issue #90 reproducer is a config error, not ``proved!``."""
    case = FIXTURES / "cases" / "omits_default.py"
    with pytest.raises(ProverConfigError) as excinfo:
        prove_path(case, trusted_roots=[FIXTURE_TRUSTED], import_roots=[FIXTURES])
    message = str(excinfo.value)
    assert "sigtrusted.effects.do_thing" in message
    assert "(x, dry_run)" in message
    assert "(x, dry_run=True)" in message


def test_mismatched_predicate_in_package_init_fails_the_trusted_load() -> None:
    """An effect declared in ``__init__.py`` is checked like any other module.

    The loader used to skip ``__init__.py`` before the check ran, so an effect
    declared there was never inspected. The case states ``no_guarantees()``
    rather than a relation over ``do_thing``: the effect registry skips
    ``__init__.py`` too, so a relation would be refused on that ground first
    and the test would not be pinning the signature check.
    """
    case = FIXTURES / "cases" / "omits_default_in_init.py"
    with pytest.raises(ProverConfigError) as excinfo:
        prove_path(case, trusted_roots=[PACKAGE_TRUSTED], import_roots=[FIXTURES])
    assert "pkgtrusted.do_thing" in str(excinfo.value)


@pytest.mark.parametrize("package", ["sigtrusted.effects", "pkgtrusted"])
def test_mismatched_predicate_raises_type_error_at_runtime(
    monkeypatch: pytest.MonkeyPatch, package: str
) -> None:
    """Pins the runtime failure the load-time check exists to prevent."""
    monkeypatch.syspath_prepend(str(FIXTURES))
    effects = importlib.import_module(package)
    with pytest.raises(TypeError, match="dry_run"):
        effects.do_thing(1)


def do_thing(x: int, dry_run: bool = True) -> None:
    pass


def notify(to: str, *, dry_run: bool = True) -> None:
    pass


def count_up_to(cap: int, *, limit: int = 5) -> int:
    return min(cap, limit)


class _Scheme:
    """Stands in for a marshmallow-style scheme: deal hands it to ``vaa``."""

    def __init__(self, data: object) -> None:
        self.data = data

    def is_valid(self) -> bool:
        return True


def _module_with(effect: object) -> ModuleType:
    module = ModuleType("variant")
    vars(module)["effect"] = effect
    return module


def _expect(ok: bool, pattern: str) -> AbstractContextManager[object]:
    return nullcontext() if ok else pytest.raises(ProverConfigError, match=pattern)


@pytest.mark.parametrize(
    ("func", "predicate", "ok"),
    [
        (do_thing, lambda x, dry_run=True: x > 0, True),
        (do_thing, lambda x, dry_run: x > 0, False),  # the #90 reproducer
        (do_thing, lambda x: x > 0, False),  # too few parameters
        (do_thing, lambda y, dry_run=True: y > 0, False),  # renamed parameter
        (do_thing, lambda x, dry_run=False: x > 0, False),  # different default
        (do_thing, lambda _: _.x > 0, True),  # deal's short form takes one object
        (notify, lambda to, *, dry_run=True: "@" in to, True),
        (notify, lambda to, dry_run=True: "@" in to, False),  # keyword-only lost
        # deal would call a var-args predicate happily, but the prover binds the
        # body by parameter name in the function's scope, so it is refused too.
        (do_thing, lambda *args, **kwargs: True, False),
        # A partial is not a function but deal still calls it with the
        # arguments; skipping it re-opened #90 for this shape.
        (do_thing, functools.partial(lambda x, dry_run: x > 0), False),
    ],
)
@pytest.mark.parametrize("spelling", [deal.pre, clauz3.requires])
def test_precondition_signatures(
    spelling: Callable[..., Callable[..., object]],
    func: Callable[..., None],
    predicate: Callable[..., object],
    ok: bool,
) -> None:
    """Both spellings reach the same check, since ``requires`` wraps ``deal.pre``."""
    with _expect(ok, rf"\.{func.__name__}: {PRE_MISMATCH}"):
        check_condition_signatures(_module_with(spelling(predicate)(func)))


@pytest.mark.parametrize(
    ("predicate", "ok"),
    [
        (lambda result: result >= 0, True),  # one parameter: deal.post(result)
        (lambda cap, *, limit=5, result: result <= cap, True),
        (lambda cap, result, *, limit=5: result <= cap, True),  # trailing result
        (lambda cap, result: result <= cap, False),  # omits the keyword-only limit
        (lambda result, cap, *, limit=5: result <= cap, False),  # result not last
        (lambda cap, limit=5, result=None: True, False),  # keyword-only lost
    ],
)
def test_postcondition_signatures(predicate: Callable[..., object], ok: bool) -> None:
    """``ensures`` gets the arguments plus ``result``; ``post`` gets the result."""
    with _expect(ok, rf"\.count_up_to: {POST_MISMATCH}"):
        check_condition_signatures(_module_with(clauz3.ensures(predicate)(count_up_to)))


def test_omitting_a_keyword_only_parameter_from_ensures_fails_at_runtime() -> None:
    """The postcondition shape of #90: proves clean on main, ``TypeError`` when the
    keyword the predicate omitted is actually passed."""
    effect = clauz3.ensures(lambda cap, result: result <= cap)(count_up_to)
    assert effect(3) == 3  # the default path binds
    with pytest.raises(TypeError, match="limit"):
        effect(3, limit=7)


@pytest.mark.parametrize(
    ("predicate", "ok"),
    [
        (lambda result: True, True),
        (lambda: True, False),  # deal.post passes the result positionally
        (lambda a, b: True, False),
        (lambda *, result: True, False),  # keyword-only cannot take it
    ],
)
def test_deal_post_takes_exactly_the_result(
    predicate: Callable[..., object], ok: bool
) -> None:
    with _expect(ok, rf"\.count_up_to: {POST_MISMATCH}"):
        check_condition_signatures(_module_with(deal.post(predicate)(count_up_to)))


def test_effect_wrapped_by_a_non_function_is_still_checked() -> None:
    """``get_contracts`` reaches the contracts through ``__wrapped__``.

    An effect whose outermost wrapper is not a plain function (here
    ``functools.lru_cache``) used to be skipped entirely by an
    ``inspect.isfunction`` prefilter, hiding a mismatched predicate.
    """
    effect = functools.lru_cache(maxsize=None)(
        clauz3.requires(lambda x, dry_run: x > 0)(do_thing)
    )
    with pytest.raises(ProverConfigError, match=PRE_MISMATCH):
        check_condition_signatures(_module_with(effect))


def test_unreadable_predicate_signature_is_refused_not_skipped() -> None:
    """A callable ``inspect.signature`` cannot read is a refusal, fail-closed."""

    class Opaque:
        def __call__(self, *args: object, **kwargs: object) -> bool:
            return True

        __signature__ = "not a signature"  # makes inspect.signature raise

    with pytest.raises(ProverConfigError, match="no readable signature"):
        check_condition_signatures(_module_with(deal.pre(Opaque())(do_thing)))


def test_unreadable_effect_signature_is_refused_not_a_traceback() -> None:
    """The function's own signature read is guarded like the predicate's."""

    class Effect:
        def __call__(self, x: int, dry_run: bool = True) -> None:
            return None

        __signature__ = "not a signature"

    with pytest.raises(ProverConfigError, match="own signature cannot be read"):
        check_condition_signatures(
            _module_with(deal.pre(lambda x, dry_run=True: True)(Effect()))
        )


def test_scheme_exemption_follows_vaa_not_the_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``vaa`` present, only what it wraps is exempt.

    ``vaa`` is not installed in this project, so the mirror of deal's
    ``_wrap_vaa`` is otherwise dead in CI. A stub that accepts only objects
    carrying ``__schema__`` pins that a plain mismatched lambda is still
    refused while a scheme is passed through.
    """

    def wrap(validator: object, simple: bool) -> object:
        if validator is _Scheme:
            return validator
        raise TypeError("not a scheme")

    stub = ModuleType("vaa")
    vars(stub)["wrap"] = wrap
    monkeypatch.setitem(sys.modules, "vaa", stub)
    monkeypatch.setattr("deal._runtime._validators.vaa", stub)

    effect = deal.pre(_Scheme)(do_thing)
    check_condition_signatures(_module_with(effect))
    effect(x=1)
    with pytest.raises(ProverConfigError, match=PRE_MISMATCH):
        check_condition_signatures(
            _module_with(deal.pre(lambda x, dry_run: x > 0)(do_thing))
        )


def test_same_named_layer_checks_selected_signatures_and_restores_ambient_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B's invalid predicate cannot borrow A's valid cached callable (#111).

    The selected layer is loaded and checked in isolation. A failed B check
    restores A's module identities, so an unchanged A can still prove afterward.
    """

    def make_root(base: Path, predicate: str) -> tuple[Path, Path]:
        root = base / "tools" / "sig" / "trusted"
        root.mkdir(parents=True)
        (root / "__init__.py").write_text("")
        (root / "effects.py").write_text(
            "import clauz3\n\n\n"
            f"@clauz3.requires({predicate})\n"
            "@clauz3.trusted\n"
            "def do_thing(x: int, dry_run: bool = True) -> None:\n"
            "    return None\n"
        )
        (root / "contracts.py").write_text(
            "from clauz3.spec import ContractSpec, contract\n"
            "from clauz3.spec import no_guarantees as core_no_guarantees\n\n\n"
            "@contract\n"
            "def no_guarantees() -> ContractSpec:\n"
            "    return core_no_guarantees()\n"
        )
        case = base / "plan.py"
        case.write_text(
            "import clauz3\n"
            "from tools.sig.trusted import contracts as sig\n"
            "from tools.sig.trusted.effects import do_thing\n\n\n"
            "@clauz3.guarantee(sig.no_guarantees())\n"
            "def main() -> None:\n"
            "    do_thing(1)\n"
        )
        return root, case

    clean_root, clean_case = make_root(tmp_path / "a", "lambda x, dry_run=True: x > 0")
    broken_root, broken_case = make_root(tmp_path / "b", "lambda x, dry_run: x > 0")
    for name in [m for m in sys.modules if m.startswith("tools.sig")]:
        del sys.modules[name]

    results = prove_path(
        clean_case, trusted_roots=[clean_root], import_roots=[tmp_path / "a"]
    )
    assert [r.proof.conclusion.name for r in results] == ["OK"]
    ambient = {
        name: module
        for name, module in sys.modules.items()
        if name == "tools" or name.startswith("tools.sig")
    }
    checked: list[ModuleType] = []

    def check_selected(module: ModuleType) -> None:
        if module.__name__ == "tools.sig.trusted.effects":
            checked.append(module)
        check_condition_signatures(module)

    monkeypatch.setattr("clauz3.prover.check_condition_signatures", check_selected)
    with pytest.raises(ProverConfigError, match=PRE_MISMATCH):
        prove_path(
            broken_case, trusted_roots=[broken_root], import_roots=[tmp_path / "b"]
        )
    assert checked
    selected = checked[-1]
    assert (
        Path(vars(selected)["__file__"]).resolve()
        == (broken_root / "effects.py").resolve()
    )
    assert selected is not ambient["tools.sig.trusted.effects"]
    assert all(sys.modules[name] is module for name, module in ambient.items())
    assert (
        vars(sys.modules["tools.sig.trusted"])["effects"]
        is ambient["tools.sig.trusted.effects"]
    )

    results = prove_path(
        clean_case, trusted_roots=[clean_root], import_roots=[tmp_path / "a"]
    )
    assert [r.proof.conclusion.name for r in results] == ["OK"]
    assert checked[-1] is ambient["tools.sig.trusted.effects"]
    assert all(sys.modules[name] is module for name, module in ambient.items())


def test_ensures_on_a_function_with_its_own_result_parameter_is_refused() -> None:
    """deal would pass ``result=`` twice, so the predicate can never be called."""

    def score(job: str, result: int, *, verbose: bool = False) -> int:
        return result

    with pytest.raises(ProverConfigError, match="parameter named 'result'"):
        check_condition_signatures(
            _module_with(
                clauz3.ensures(lambda job, result, *, verbose=False: True)(score)
            )
        )


def test_ensures_predicate_with_no_positional_parameters() -> None:
    """A keyword-only-only predicate is compared without indexing an empty list."""

    def tick(*, limit: int = 5) -> int:
        return limit

    check_condition_signatures(
        _module_with(clauz3.ensures(lambda *, limit=5, result: result <= limit)(tick))
    )
    with pytest.raises(ProverConfigError, match=POST_MISMATCH):
        check_condition_signatures(
            _module_with(clauz3.ensures(lambda *, result: True)(tick))
        )


def test_positional_result_before_varargs_is_refused() -> None:
    def add(first: int, *rest: int) -> int:
        return first + sum(rest)

    effect = clauz3.ensures(lambda first, result, *rest: result >= first)(add)
    with pytest.raises(TypeError, match="multiple values.*result"):
        effect(1, 2)
    with pytest.raises(ProverConfigError, match=POST_MISMATCH):
        check_condition_signatures(_module_with(effect))

    supported = clauz3.ensures(lambda first, *rest, result: result >= first)(add)
    check_condition_signatures(_module_with(supported))
    assert supported(1, 2) == 3


@pytest.mark.parametrize("predicate", [lambda *, _: True, lambda **_: True])
def test_short_form_must_accept_a_positional_mapping(
    predicate: Callable[..., object],
) -> None:
    effect = clauz3.requires(predicate)(do_thing)
    with pytest.raises(TypeError, match="positional"):
        effect(1)
    with pytest.raises(ProverConfigError, match="one positional mapping"):
        check_condition_signatures(_module_with(effect))


@pytest.mark.parametrize(
    ("function", "arguments"), [(lambda: 1, ()), (lambda a, b: a + b, (1, 2))]
)
def test_mapping_postcondition_must_bind_the_result_to_the_function(
    function: Callable[..., int], arguments: tuple[int, ...]
) -> None:
    effect = deal.post(lambda _: True)(function)
    with pytest.raises(TypeError):
        effect(*arguments)
    with pytest.raises(ProverConfigError, match="mapping postcondition"):
        check_condition_signatures(_module_with(effect))


def test_mapping_precondition_refuses_reserved_result_parameter() -> None:
    def score(result: int) -> int:
        return result

    effect = clauz3.requires(lambda _: True)(score)
    with pytest.raises(TypeError, match="result"):
        effect(result=1)
    with pytest.raises(ProverConfigError, match="mapping condition.*'result'"):
        check_condition_signatures(_module_with(effect))


def test_is_valid_does_not_exempt_a_schema_without_the_runtime_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("deal._runtime._validators.vaa", None)
    effect = deal.pre(_Scheme)(do_thing)
    with pytest.raises(TypeError, match="unexpected keyword argument 'x'"):
        effect(x=1)
    with pytest.raises(ProverConfigError, match=PRE_MISMATCH):
        check_condition_signatures(_module_with(effect))


@pytest.mark.parametrize("partial", [False, True])
def test_permissive_adapter_cannot_exempt_an_explicit_predicate(
    monkeypatch: pytest.MonkeyPatch, partial: bool
) -> None:
    def condition(x: int, dry_run: bool) -> bool:
        return x > 0

    predicate: Callable[..., object] = condition
    if partial:
        predicate = functools.partial(predicate)
    stub = ModuleType("vaa")
    vars(stub)["wrap"] = lambda validator, simple: _Scheme
    monkeypatch.setitem(sys.modules, "vaa", stub)
    monkeypatch.setattr("deal._runtime._validators.vaa", stub)
    with pytest.raises(ProverConfigError, match=PRE_MISMATCH):
        check_condition_signatures(_module_with(clauz3.requires(predicate)(do_thing)))


def test_adapter_failure_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def wrap(validator: object, simple: bool) -> object:
        raise ValueError("broken schema adapter")

    stub = ModuleType("vaa")
    vars(stub)["wrap"] = wrap
    monkeypatch.setattr("deal._runtime._validators.vaa", stub)
    with pytest.raises(
        ProverConfigError, match="schema adapter.*broken schema adapter"
    ):
        check_condition_signatures(_module_with(deal.pre(_Scheme)(do_thing)))


@pytest.mark.parametrize(
    "initializer",
    [
        "import clauz3\n@clauz3.requires(lambda x, dry_run: True)\n"
        "def effect(x: int, dry_run: bool = True) -> None:\n    pass\n",
        "import clauz3\n@clauz3.trusted\ndef effect() -> None:\n    pass\n",
    ],
)
def test_bare_initializer_declarations_are_refused(
    tmp_path: Path, initializer: str
) -> None:
    (tmp_path / "__init__.py").write_text(initializer)
    with pytest.raises(ProverConfigError, match="bare __init__.*--import-root"):
        prove_text(
            "def main() -> None:\n    assert True\n",
            trusted_roots=[tmp_path],
            import_paths=[tmp_path],
        )


@pytest.mark.parametrize("initializer", ["", '"""An empty layer."""\npass\n'])
def test_empty_bare_initializer_is_supported(tmp_path: Path, initializer: str) -> None:
    (tmp_path / "__init__.py").write_text(initializer)
    results = prove_text(
        "def main() -> None:\n    assert True\n",
        trusted_roots=[tmp_path],
        import_paths=[tmp_path],
    )
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


@pytest.mark.parametrize(
    ("trusted_root", "case_name"),
    [
        (FIXTURE_TRUSTED, "omits_default.py"),
        (PACKAGE_TRUSTED, "omits_default_in_init.py"),
    ],
)
def test_signature_refusal_stops_before_approval_or_execution(
    monkeypatch: pytest.MonkeyPatch, trusted_root: Path, case_name: str
) -> None:
    case = FIXTURES / "cases" / case_name
    approve = Mock(side_effect=AssertionError("invalid layer reached approval"))
    execute = Mock(side_effect=AssertionError("invalid layer reached execution"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute)
    with pytest.raises(RunConfigError, match=PRE_MISMATCH):
        run_source(
            case.read_text(),
            source_name=str(case),
            trusted_roots=[trusted_root],
            import_roots=[FIXTURES],
            approval_service_url="http://approval.invalid",
        )
    approve.assert_not_called()
    execute.assert_not_called()
