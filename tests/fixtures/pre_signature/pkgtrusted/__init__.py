"""Trusted layer whose effect lives in the package ``__init__.py``.

The predicate omits ``dry_run`` exactly as ``sigtrusted.effects.do_thing``
does. The loader used to skip ``__init__.py`` before running the signature
check, so an effect declared here was never inspected (review on #146). No
contract builds a relation over it: the effect registry also skips
``__init__.py``, so a relation would be refused for that reason first and
the case would not isolate the signature check.
"""

import clauz3


@clauz3.requires(lambda x, dry_run: x > 0, message="x must be positive")
@clauz3.trusted
def do_thing(x: int, dry_run: bool = True) -> None:
    """MOCK trusted effect declared at package level."""
    if dry_run:
        return
    raise AssertionError("real effect would run here")
