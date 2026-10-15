"""Operator-owned authorization for layers loaded by approval policies.

Request paths select a configured layer; they never grant loading authority.
The operator must control the code in these roots and the service environment.
Authorizing paths does not sandbox their contents or make writable code safe.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class ApprovalAuthorizationError(Exception):
    """The request does not select an operator-authorized layer."""


@dataclass(frozen=True)
class ApprovedLayer:
    """An exact, ordered pair of root lists approved by the service operator.

    Resolve configured paths at construction, before handling requests. Do not
    construct this configuration from an incoming request's root fields.
    """

    trusted_roots: tuple[Path, ...]
    import_roots: tuple[Path, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "trusted_roots", _canonical_paths(self.trusted_roots))
        object.__setattr__(self, "import_roots", _canonical_paths(self.import_roots))


def authorize_layer(
    request: Mapping[str, object],
    *,
    authorized_layers: Sequence[ApprovedLayer],
) -> ApprovedLayer:
    """Match request roots without importing code and return operator paths.

    Both lists must match one complete configured pair. Independently allowing
    roots, descendants, or a different order would authorize a new import graph.
    """

    if not authorized_layers:
        raise ApprovalAuthorizationError(
            "no trusted layers are authorized for approval policy evaluation"
        )
    trusted_roots = _request_paths(request, "trusted_roots")
    import_roots = _request_paths(request, "import_roots")
    for layer in authorized_layers:
        if trusted_roots == layer.trusted_roots and import_roots == layer.import_roots:
            return layer
    raise ApprovalAuthorizationError(
        "requested trusted_roots and import_roots do not match an authorized layer"
    )


def _request_paths(request: Mapping[str, object], field: str) -> tuple[Path, ...]:
    value = request.get(field)
    if not isinstance(value, list):
        raise ApprovalAuthorizationError(f"{field} must be a list of path strings")
    paths: list[Path] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ApprovalAuthorizationError(
                f"{field} must contain only nonempty path strings"
            )
        paths.append(Path(item))
    return _canonical_paths(paths)


def _canonical_paths(paths: Sequence[Path]) -> tuple[Path, ...]:
    try:
        return tuple(path.resolve() for path in paths)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ApprovalAuthorizationError(
            "could not resolve approval layer paths"
        ) from exc
