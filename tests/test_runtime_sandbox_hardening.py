"""Where the issue #68 escape catalogue is tested now.

This module used to check ``clauz3.runner._validate_runtime_source``'s AST
denylist -- ``FORBIDDEN_BUILTINS``, ``FORBIDDEN_ATTRIBUTES`` -- and its own
docstring described that as "harm-reduction stopgaps, not a security boundary
-- the only real fix is OS-level isolation".

The runtime is no longer ``exec``, so there is no denylist left to test. Every
payload it carried is now asserted against the interpreter in
``tests/test_interp_escapes.py``, which keeps the two lists as a catalogue and
checks something stronger: not that each name is refused, but that none of them
resolves to anything, and that none is named anywhere in ``clauz3/interp/``.

``test_operator_no_longer_default_allowed`` moved to
``tests/test_imports_policy.py``: it is a statement about the import allowlist,
which survives, rather than about the executor, which does not.
"""
