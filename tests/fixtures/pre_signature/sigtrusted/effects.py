"""Trusted layer whose precondition predicate omits a declared default.

This is the reproducer from issue #90 and must stay broken: the predicate
``lambda x, dry_run`` cannot accept ``do_thing(1)``, which ``do_thing`` accepts.
It is written in the current ``@clauz3.requires`` spelling; ``requires``
delegates to ``deal.pre``, so a layer still spelled ``@deal.pre`` fails the
same way (see ``tests/test_pre_validator_signature.py``).
"""

import clauz3


@clauz3.requires(lambda x, dry_run: x > 0, message="x must be positive")
@clauz3.trusted
def do_thing(x: int, dry_run: bool = True) -> None:
    """MOCK trusted effect with the safe-default pattern."""
    if dry_run:
        return
    raise AssertionError("real effect would run here")
