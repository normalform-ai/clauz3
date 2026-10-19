"""Historical presentation of selected contracts, independent of agent aliases."""

from __future__ import annotations

import ast
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from clauz3.libraries.manifest import ManifestError, read_manifest
from clauz3.libraries.mock_metadata import mock_declarations


@dataclass(frozen=True)
class _Argument:
    text: str


def _argument(node: ast.expr, aliases: Mapping[str, str]) -> _Argument:
    class Resolve(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.expr:
            return ast.parse(aliases.get(node.id, node.id), mode="eval").body

    return _Argument(
        ast.unparse(Resolve().visit(ast.parse(ast.unparse(node), mode="eval").body))
    )


def _value(value: object) -> str:
    if isinstance(value, _Argument):
        return value.text
    if isinstance(value, tuple):
        return (
            "("
            + ", ".join(_value(v) for v in value)
            + ("," if len(value) == 1 else "")
            + ")"
        )
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k!r}: {_value(v)}" for k, v in value.items()) + "}"
    return repr(value)


def identify_call(
    owner: Callable[..., object],
    expression: ast.Call,
    *,
    aliases: Mapping[str, str],
    roots: Sequence[Path],
    sources: Mapping[str, str],
) -> dict[str, object]:
    """Snapshot only an already resolved callable in the verified source inventory.

    Never import by an agent-provided display name or call the contract body.
    Labels and origins are library declarations; hashes identify the proved code.
    """
    filename = inspect.getsourcefile(owner)
    if filename is None:
        raise ValueError("Contract has no inspectable source")
    path = Path(filename).resolve()
    digest = sources.get(str(path))
    if digest is None or sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("Contract source differs from the verified layer")
    root = next((p for p in roots if path.is_relative_to(p.resolve())), None)
    if root is None:
        raise ValueError("Contract is outside the selected trusted roots")
    symbol = f"{owner.__module__}.{owner.__name__}"
    namespace = owner.__module__.split(".trusted", 1)[0].removeprefix("tools.")
    signature = inspect.signature(owner)
    bound = signature.bind(
        *[_argument(arg, aliases) for arg in expression.args],
        **{
            kw.arg: _argument(kw.value, aliases) for kw in expression.keywords if kw.arg
        },
    )
    bound.apply_defaults()
    has_varargs = any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL and bound.arguments.get(name)
        for name, parameter in signature.parameters.items()
    )
    rendered = []
    for name, value in bound.arguments.items():
        kind = signature.parameters[name].kind
        if kind is inspect.Parameter.POSITIONAL_ONLY or (
            kind is inspect.Parameter.POSITIONAL_OR_KEYWORD and has_varargs
        ):
            rendered.append(_value(value))
        elif kind is inspect.Parameter.VAR_POSITIONAL:
            if value:
                rendered.append(f"*{_value(value)}")
        elif kind is inspect.Parameter.VAR_KEYWORD:
            if value:
                rendered.append(f"**{_value(value)}")
        else:
            rendered.append(f"{name}={_value(value)}")
    arguments = ", ".join(rendered)
    lines, line = inspect.getsourcelines(owner)
    identity: dict[str, object] = {
        "symbol": symbol,
        "call": f"{namespace}.{owner.__name__}({arguments})",
        "library": namespace,
        "title": namespace,
        "source_file": str(path),
        "source_line": line,
        "source_code": "".join(lines),
        "source_sha256": digest,
        "layer_sha256": sha256(
            json.dumps(dict(sources), sort_keys=True).encode()
        ).hexdigest(),
        "origin": "Local library; origin not recorded",
        "revision": "",
        "mock": mock_declarations(None, [root])["kind"],
    }
    try:
        manifest = read_manifest(root.parent, namespace.replace(".", "/"))
        if manifest:
            identity["title"] = manifest["title"]
    except (ManifestError, OSError, ValueError):
        pass  # Descriptive metadata cannot substitute for verified identity.
    origin = root.parent / ".clauz3-origin.json"
    try:
        if (
            origin.is_file()
            and not origin.is_symlink()
            and origin.stat().st_size <= 128_000
        ):
            metadata = json.loads(origin.read_text())
            if isinstance(metadata, dict) and isinstance(metadata.get("source"), str):
                identity["origin"] = metadata["source"]
                if isinstance(metadata.get("revision"), str):
                    identity["revision"] = metadata["revision"]
    except (OSError, ValueError):
        pass
    return identity
