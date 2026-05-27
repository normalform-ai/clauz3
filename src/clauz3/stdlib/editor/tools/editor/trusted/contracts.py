"""Editor path and content policies built from generic trusted effect facts.

Edits (both full-replace and append) are recorded under the ``edit`` marker.
These contracts let an agent state where on the filesystem it may edit, how
many edits it may make, and constraints on the content written.
"""

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Edit = effect("edit")
EditFile = effect("edit_file")
AppendFile = effect("append_file")


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about editor effects."""
    return core_no_guarantees()


@contract
def no_edits() -> ContractSpec:
    """Guarantee the program performs no edits (replace or append)."""
    return Edit.empty()


@contract
def no_appends() -> ContractSpec:
    """Guarantee the program performs no appends."""
    return AppendFile.empty()


@contract
def only_edit_under(root: str) -> ContractSpec:
    """Guarantee every edit (replace or append) has a path under ``root``."""
    return Edit.all(lambda e: e.path.startswith(root))


@contract
def never_edit_under(prefix: str) -> ContractSpec:
    """Guarantee no edit has a path under ``prefix``."""
    return Edit.all(lambda e: not e.path.startswith(prefix))


@contract
def edits_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` edits occur."""
    return Edit.count() <= count


@contract
def replace_length_at_most(max_chars: int) -> ContractSpec:
    """Guarantee every ``edit_file`` replacement is at most ``max_chars`` long.

    Bounds full-content replacements; ``append_file`` is unaffected and uses
    ``append_length_at_most`` for the corresponding bound.
    """
    return EditFile.all(lambda e: len(e.new_text) <= max_chars)


@contract
def append_length_at_most(max_chars: int) -> ContractSpec:
    """Guarantee every ``append_file`` chunk is at most ``max_chars`` long."""
    return AppendFile.all(lambda e: len(e.text) <= max_chars)


@contract
def must_not_replace(substring: str) -> ContractSpec:
    """Guarantee no full-replace contains ``substring`` (e.g. a banned token).

    Useful as a secrets / banned-content guard before content reaches disk.
    Applies to ``edit_file`` only; the append-side guard is ``must_not_append``.
    """
    return EditFile.all(lambda e: substring not in e.new_text)


@contract
def must_not_append(substring: str) -> ContractSpec:
    """Guarantee no append chunk contains ``substring``."""
    return AppendFile.all(lambda e: substring not in e.text)
