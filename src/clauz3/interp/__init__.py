"""Interpreter for the clauz3-proved Python subset.

``clauz3.runner`` proves a program and then runs it here, over the same
astroid tree the prover walked, so a construct outside the proved subset has
no handler and cannot execute. This replaced the ``exec``-plus-denylist path;
see ``docs/todos/subset-interpreter.md`` for the rationale.
"""

from .errors import (
    ExecutionLimitExceeded,
    InterpError,
    NameResolutionError,
    ProgramError,
    ProgramFault,
    TrustedBoundaryError,
    UnsupportedNode,
)
from .interpreter import (
    DEFAULT_STEP_BUDGET,
    Effect,
    Outcome,
    check_module_level,
    eval_expr,
    eval_stmt,
    execute,
    target_node,
    validate_import_origins,
)

__all__ = [
    "DEFAULT_STEP_BUDGET",
    "Effect",
    "ExecutionLimitExceeded",
    "InterpError",
    "NameResolutionError",
    "Outcome",
    "ProgramError",
    "ProgramFault",
    "TrustedBoundaryError",
    "UnsupportedNode",
    "check_module_level",
    "eval_expr",
    "eval_stmt",
    "execute",
    "target_node",
    "validate_import_origins",
]
