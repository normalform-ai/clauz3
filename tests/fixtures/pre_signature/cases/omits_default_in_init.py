# ruff: noqa
import clauz3
from pkgtrusted import contracts as thing
from pkgtrusted import do_thing


@clauz3.guarantee(thing.no_guarantees())
def main() -> None:
    do_thing(1)  # omits dry_run, relying on the documented default
