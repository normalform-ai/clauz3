from __future__ import annotations

import inspect
import sys
import typing
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import MappingProxyType, ModuleType


T = typing.TypeVar('T', bound=typing.Callable)
FUNCTIONS: dict[str, typing.Any] = dict()
_ENGINE_FUNCTIONS: dict[str, typing.Any] = {}
ENGINE_FUNCTIONS = MappingProxyType(_ENGINE_FUNCTIONS)
_engine_initialized = False


@dataclass(frozen=True)
class Registration:
    """A model's original declaring module, retained across layer switches.

    Contract adapters live in clauz3.spec, so their owner must be supplied
    explicitly. A record alone grants no authority: the layer coordinator
    must verify the module identity and source before selecting it.
    """

    name: str
    model: typing.Callable
    owner: typing.Callable
    module: ModuleType | None
    origin: Path | None
    is_contract: bool


REGISTRATIONS: list[Registration] = []


@dataclass(frozen=True)
class RegisteredFunction:
    """A model and its explicitly supported Python-facing call signature."""

    name: str
    impl: typing.Callable
    public_signature: inspect.Signature | None

    def __call__(self, *args, **kwargs):
        # Model-to-model calls already supply the internal context themselves.
        # Source calls go through FuncSort's public argument validation.
        return self.impl(*args, **kwargs)


def init_all():
    global _engine_initialized
    import_module('._builtins', package=__package__)
    import_module('._math', package=__package__)
    import_module('._os_path', package=__package__)
    import_module('._random', package=__package__)
    import_module('._re', package=__package__)
    # Capture before any user decorator can run. A later init_all call must
    # not promote ambient user models into permanent engine registrations.
    if not _engine_initialized:
        _ENGINE_FUNCTIONS.update(FUNCTIONS)
        _engine_initialized = True


def register(
    name: str, *, owner: typing.Callable | None = None, is_contract: bool = False,
    signature: typing.Callable | inspect.Signature | None = None,
) -> typing.Callable[[T], T]:
    def wrapper(func: T) -> T:
        public_signature = (
            signature
            if isinstance(signature, inspect.Signature) or signature is None
            else inspect.signature(signature)
        )
        # Aliases retain their shared model identity while each declaration
        # still gets an ownership record for trusted-layer isolation.
        model = next((
            registered for registered in FUNCTIONS.values()
            if isinstance(registered, RegisteredFunction)
            and registered.impl is func
            and registered.public_signature == public_signature
        ), None)
        if model is None:
            model = RegisteredFunction(name, func, public_signature)
        FUNCTIONS[name] = model
        declaration = func if owner is None else owner
        module = sys.modules.get(getattr(declaration, '__module__', ''))
        if type(module) is not ModuleType or (
            getattr(declaration, '__globals__', None) is not vars(module)
        ):
            module = None
        code = getattr(declaration, '__code__', None)
        filename = getattr(code, 'co_filename', None)
        origin = (
            Path(filename).resolve()
            if type(filename) is str and not filename.startswith('<')
            else None
        )
        REGISTRATIONS.append(Registration(
            name=name, model=model, owner=declaration, module=module,
            origin=origin, is_contract=is_contract,
        ))
        return func
    return wrapper
