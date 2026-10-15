# ruff: noqa
# `open_session` returns a type the solver cannot model; it must not be
# silently assumed to be a truthy value.
import clauz3
from rtrusted import contracts
from rtrusted.effects import open_session


@clauz3.guarantee(contracts.none())
def main() -> None:
    open_session("alice")
