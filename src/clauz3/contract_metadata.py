"""Trusted, deterministic contract presentation without a LinkML dependency.

Templates live in trusted Python source. The same signatures and templates
can be exported as LinkML by :mod:`clauz3.libraries.contract_schema`.
"""

import inspect
import json
from collections.abc import Callable, Mapping
from hashlib import sha256
from string import Formatter

RENDERER_VERSION = 1
DESCRIPTION_ATTRIBUTE = "__clauz3_description__"
SCALAR_TYPES: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
}


def parameter_types(signature: inspect.Signature) -> dict[str, str]:
    """The deliberately small typed parameter language supported by CNL v1.

    A parameter is a scalar or a list of one scalar type.

    >>> parameter_types(inspect.signature(lambda modes, path: None))
    Traceback (most recent call last):
    ...
    ValueError: unsupported description parameter type: modes
    >>> def only_modes(path: str, modes: list[int]) -> None: ...
    >>> parameter_types(inspect.signature(only_modes))
    {'path': 'str', 'modes': 'list[int]'}
    """
    result = {}
    for name, parameter in signature.parameters.items():
        if parameter.kind in {parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD}:
            raise ValueError("contract descriptions do not support variadic parameters")
        annotation = parameter.annotation
        kind = next(
            (
                key
                for key, scalar in SCALAR_TYPES.items()
                if annotation is scalar or annotation == key
            ),
            None,
        ) or next(
            (
                f"list[{key}]"
                for key, scalar in SCALAR_TYPES.items()
                if annotation == list[scalar] or annotation == f"list[{key}]"  # type: ignore[valid-type]
            ),
            None,
        )
        if kind is None:
            raise ValueError(f"unsupported description parameter type: {name}")
        if parameter.default is not parameter.empty:
            format_literal(parameter.default, kind)
        result[name] = kind
    return result


def validate_template(template: str, parameters: Mapping[str, str]) -> None:
    """Accept only named parameter substitutions, with every parameter visible."""
    if not isinstance(template, str) or not template.strip():
        raise ValueError("a contract description must be a nonempty string")
    referenced = set()
    for _, field, spec, conversion in Formatter().parse(template):
        if field is not None:
            if field not in parameters or spec or conversion:
                raise ValueError(f"unsupported description placeholder: {field!r}")
            referenced.add(field)
    if referenced != set(parameters):
        raise ValueError("a contract description must include every parameter")


def format_literal(value: object, kind: str) -> str:
    """Quote strings and reject coercions (notably bool masquerading as int).

    >>> format_literal([1, 2], "list[int]")
    '[1, 2]'
    >>> format_literal([1, True], "list[int]")
    Traceback (most recent call last):
    ...
    ValueError: expected list[int] description parameter
    """
    element = element_kind(kind)
    if element is None:
        valid = type(value) is SCALAR_TYPES[kind]
    else:
        valid = type(value) is list and all(
            type(item) is SCALAR_TYPES[element] for item in value
        )
    if not valid:
        raise ValueError(f"expected {kind} description parameter")
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def element_kind(kind: str) -> str | None:
    """The scalar kind of a list parameter kind, or ``None`` for a scalar.

    >>> element_kind("list[str]"), element_kind("str")
    ('str', None)
    """
    if kind.startswith("list[") and kind.endswith("]"):
        return kind[len("list[") : -1]
    return None


def describe_call(
    owner: Callable[..., object],
    *,
    args: list[object],
    kwargs: dict[str, object],
) -> dict[str, object] | None:
    """Bind literal arguments to a selected trusted helper; never call its body.

    Missing or unsupported metadata yields no generated sentence. Callers keep
    the original expression and docstring as the fallback presentation.
    """
    template = getattr(owner, DESCRIPTION_ATTRIBUTE, None)
    if not isinstance(template, str):
        return None
    try:
        signature = inspect.signature(owner)
        kinds = parameter_types(signature)
        validate_template(template, kinds)
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        values = {
            name: format_literal(bound.arguments[name], k) for name, k in kinds.items()
        }
        parts = []
        for literal, field, _, _ in Formatter().parse(template):
            parts.append(literal)
            if field is not None:
                parts.append(values[field])
    except (TypeError, ValueError):
        return None
    return {
        "renderer_version": RENDERER_VERSION,
        "symbol": f"{owner.__module__}.{owner.__name__}",
        "parameters": dict(bound.arguments),
        "template_sha256": sha256(template.encode()).hexdigest(),
        "text": "".join(parts),
    }
