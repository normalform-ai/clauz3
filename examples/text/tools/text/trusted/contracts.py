"""String-manipulation predicates built from generic trusted effect facts.

Sent text is recorded under the ``send_message`` relation and file edits under
``edit_file`` (see ``effects.py``). These contracts let an agent state policies
about the *shape* of text it sends or writes:

- length bounds (``length_at_most`` / ``length_at_least`` / ``length_between``);
- required or banned substrings (``must_contain`` / ``must_not_contain``);
- freedom from regex metacharacters before text is fed to a pattern matcher
  (``no_regex_metacharacters``);
- where on disk an edit may land and how large it may be
  (``only_edit_under`` / ``edit_length_at_most``).

The length checks compile to ``z3.Length``, the substring checks to
``z3.Contains``, and the path-prefix checks to ``z3.PrefixOf`` via the
relation-lambda support in ``clauz3.spec``.
"""

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Message = effect("send_message")
Edit = effect("edit_file")


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about text effects."""
    return core_no_guarantees()


@contract
def length_at_most(max_chars: int) -> ContractSpec:
    """Guarantee no sent text is longer than ``max_chars`` characters."""
    return Message.all(lambda e: len(e.text) <= max_chars)


@contract
def length_at_least(min_chars: int) -> ContractSpec:
    """Guarantee no sent text is shorter than ``min_chars`` characters."""
    return Message.all(lambda e: len(e.text) >= min_chars)


@contract
def length_between(min_chars: int, max_chars: int) -> ContractSpec:
    """Guarantee every sent text has length within ``[min_chars, max_chars]``."""
    return Message.all(lambda e: min_chars <= len(e.text) and len(e.text) <= max_chars)


@contract
def must_contain(substring: str) -> ContractSpec:
    """Guarantee every sent text contains ``substring`` (e.g. a required footer)."""
    return Message.all(lambda e: substring in e.text)


@contract
def must_not_contain(substring: str) -> ContractSpec:
    """Guarantee no sent text contains ``substring`` (e.g. a banned token)."""
    return Message.all(lambda e: substring not in e.text)


@contract
def no_regex_metacharacters() -> ContractSpec:
    """Guarantee no sent text contains a regular-expression metacharacter.

    Useful before passing agent- or user-influenced text into a system that
    treats it as (or interpolates it into) a regex: it rules out both pattern
    injection and pathological ``ReDoS`` constructs by construction.
    """
    return Message.all(
        lambda e: (
            "\\" not in e.text
            and "." not in e.text
            and "^" not in e.text
            and "$" not in e.text
            and "*" not in e.text
            and "+" not in e.text
            and "?" not in e.text
            and "(" not in e.text
            and ")" not in e.text
            and "[" not in e.text
            and "]" not in e.text
            and "{" not in e.text
            and "}" not in e.text
            and "|" not in e.text
        )
    )


@contract
def sends_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` messages are sent."""
    return Message.count() <= count


@contract
def no_edits() -> ContractSpec:
    """Guarantee the program edits no files."""
    return Edit.empty()


@contract
def only_edit_under(root: str) -> ContractSpec:
    """Guarantee every file edit has a path under ``root``."""
    return Edit.all(lambda e: e.path.startswith(root))


@contract
def edit_length_at_most(max_chars: int) -> ContractSpec:
    """Guarantee no file is rewritten with more than ``max_chars`` characters."""
    return Edit.all(lambda e: len(e.new_text) <= max_chars)
