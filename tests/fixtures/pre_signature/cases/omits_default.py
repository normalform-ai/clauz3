# ruff: noqa
import clauz3
from sigtrusted import contracts as thing
from sigtrusted.effects import do_thing


@clauz3.guarantee(thing.calls_at_most(1))
def main() -> None:
    do_thing(1)  # omits dry_run, relying on the documented default
