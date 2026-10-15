"""Check a trusted layer's declarations against what its bodies actually do.

The prover never runs a trusted body, so nothing in a proof notices that an
effect declaring ``read`` also opens a socket, or that one promising a positive
result returns ``-1``. deal's linter is what notices: it walks the body, infers
its side effects, resolves its calls, and reports what the declarations do not
survive --- ``DEL050 missed marker (syscall)`` for a ``subprocess.run``,
``DEL012 post contract error`` for the ``-1``.

Markers are deal's, and so is the check over them: a trusted layer adds
``@deal.has(...)`` alongside ``@clauz3.trusted`` when it wants deal's runtime
guard and that check as a second layer of defence, and a function carrying no
``@deal.has`` is simply not marker-checked, here or under ``deal lint``. The
contract rules are not so limited --- they are about what a body does versus
what it promises, which every layer declares.

What the linter reads is the *text*: its extractor matches ``deal.pre``,
``deal.post`` and friends literally, so ``@clauz3.requires(...)``, an aliased
``@d.has(...)`` and a ``predicate=`` keyword are all invisible to it, and the
checks over them silently do not run. The prover resolves those spellings
through the import bindings; this module gives the linter the same reach by
running it a second time over a *copy of the source* in which every contract is
spelled the one way it recognises.

A copy of the source, rather than a rewritten AST, because deal re-reads the
file with astroid whenever it is given a real filename, and that inference is
the whole of some checks: ``DEL011`` fires on a call whose argument the
callee's precondition forbids, which needs the callee resolved. Handing deal a
tree (``filename="stdin"``) skips that. So the rewrite is textual and edits
only decorator names in place, padding a shorter replacement with spaces, which
keeps every line where the author left it --- a violation's row is the row you
wrote, and columns move only within a rewritten decorator's own line.
"""

from __future__ import annotations

import ast
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from deal.linter import Checker

from clauz3 import decorators as decorators_mod

#: deal's name for a contract's predicate parameter. ``clauz3.conditions``
#: calls it ``predicate``, and deal's linter reads neither spelling positionally
#: unless it is the one it knows -- so the rewrite renames it. The two words are
#: the same length, which is why this edit cannot move anything.
_DEAL_PREDICATE_KEYWORD = "validator"


class LintError(Exception):
    """A path could not be linted."""


@dataclass(frozen=True)
class Violation:
    path: Path
    row: int
    col: int
    code: str
    text: str
    value: str
    content: str

    def format(self) -> str:
        suffix = f" ({self.value})" if self.value else ""
        return f"{self.path}:{self.row}:{self.col} {self.code} {self.text}{suffix}"


def lint_paths(paths: Iterable[Path]) -> list[Violation]:
    """Report every deal-linter violation in these files or directories."""

    return [violation for path in _python_files(paths) for violation in lint_file(path)]


def lint_file(path: Path) -> list[Violation]:
    """Report every deal-linter violation in one file."""

    try:
        source = path.read_text()
    except OSError as exc:
        raise LintError(f"{path}: {exc}") from exc
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise LintError(f"{path}: {exc}") from exc

    lines = source.split("\n")
    seen: set[tuple[str, int, int, str]] = set()
    violations: list[Violation] = []
    # Two passes, deduplicated: deal's own view of the file as written, then the
    # same file with every contract spelled deal's way. The second is additive,
    # so nothing deal already caught can be lost by a rewrite going wrong.
    for source_to_lint, filename in _passes(source, tree, path):
        for error in Checker(
            filename=filename, tree=ast.parse(source_to_lint)
        ).get_errors():
            key = (error.full_code, error.row, error.col, error.value or "")
            if key in seen:
                continue
            seen.add(key)
            violations.append(
                Violation(
                    path=path,
                    row=error.row,
                    col=error.col,
                    code=error.full_code,
                    text=error.text,
                    value=error.value or "",
                    content=_line(lines, error.row),
                )
            )
    return sorted(violations, key=lambda v: (v.row, v.col, v.code))


def _line(lines: Sequence[str], row: int) -> str:
    """The source line a violation points at, or ``""`` if it points nowhere.

    Rows are 1-based, so a 0 is not the last line; deal has no rule that
    reports one, and indexing ``lines[-1]`` for it would quietly attribute a
    violation to the wrong code.
    """

    return lines[row - 1] if 1 <= row <= len(lines) else ""


def _passes(source: str, tree: ast.Module, path: Path) -> Iterator[tuple[str, str]]:
    """The source and filename for each lint pass.

    The second pass's file is a copy under a temporary directory, kept alive
    only while it is being linted. It has to exist on disk because deal reads
    the file itself when it is given a real filename, and it has to *be* a real
    filename because that is what selects the astroid front end.
    """

    yield source, str(path)
    rewritten = _deal_spelling(source, tree)
    if rewritten == source:
        return
    with tempfile.TemporaryDirectory() as directory:
        copy = Path(directory) / path.name
        copy.write_text(rewritten)
        yield rewritten, str(copy)


def _deal_spelling(source: str, tree: ast.Module) -> str:
    """The same source, with every contract spelled the way deal's linter reads.

    ``@clauz3.requires(...)`` becomes ``@deal.pre(...)``, ``@clauz3.ensures``
    becomes ``@deal.post`` or ``@deal.ensure`` by the arity of its predicate
    (the same choice :mod:`clauz3.conditions` makes at runtime), a contract
    written through an import alias becomes the dotted spelling it resolves to,
    and a ``predicate=`` keyword becomes deal's ``validator=``.
    ``@clauz3.trusted`` is left alone: it declares a proof boundary and says
    nothing deal can check.

    Only names are replaced, never moved, so the result has the author's own
    line structure. A predicate is renamed rather than made positional, because
    ``requires(message="...", predicate=...)`` has nowhere positional to go.
    """

    edits: list[tuple[int, int, bytes]] = []
    aliases = decorators_mod.import_aliases(tree)
    # In bytes throughout, because ``col_offset`` is a utf-8 offset into the
    # line rather than a character index.
    data = source.encode()
    starts = _line_starts(data)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                _spell_as_deal(decorator, aliases, starts, edits)
    if not edits:
        return source
    for start, end, replacement in sorted(edits, reverse=True):
        data = data[:start] + replacement + data[end:]
    rewritten = data.decode()
    try:
        ast.parse(rewritten)
    except SyntaxError:  # pragma: no cover - a bug in the rewrite above
        return source
    return rewritten


def _spell_as_deal(
    node: ast.expr,
    aliases: Mapping[str, str],
    starts: Sequence[int],
    edits: list[tuple[int, int, bytes]],
) -> None:
    canonical = decorators_mod.canonical_target(
        decorators_mod.dotted_name(node), aliases
    )
    if canonical == "deal.chain":
        # The bundle has to be recognised before anything inside it can be.
        _rename_callee(node, "chain", starts, edits)
        for argument in getattr(node, "args", []):
            _spell_as_deal(argument, aliases, starts, edits)
        return
    name = decorators_mod.condition_contract(node, aliases)
    if name is None:
        return
    _rename_callee(node, name, starts, edits)
    _rename_predicate_keyword(node, starts, edits)


def _rename_callee(
    node: ast.expr,
    name: str,
    starts: Sequence[int],
    edits: list[tuple[int, int, bytes]],
) -> None:
    if not isinstance(node, ast.Call):
        return
    span = _span(node.func, starts)
    if span is None:
        return  # a callee split over lines; rewriting it would move code
    start, end = span
    # Padded when shorter (``deal.pre`` for ``clauz3.requires``) so the rest of
    # the line does not move. A longer one (``deal.has`` for ``d.has``) shifts
    # the columns after it, on that line only.
    edits.append((start, end, f"deal.{name}".encode().ljust(end - start)))


def _rename_predicate_keyword(
    node: ast.expr,
    starts: Sequence[int],
    edits: list[tuple[int, int, bytes]],
) -> None:
    """Give a keyword-passed predicate the name deal's linter looks for."""

    for keyword in getattr(node, "keywords", None) or []:
        if keyword.arg not in decorators_mod.PREDICATE_KEYWORDS:
            continue
        if keyword.lineno != keyword.end_lineno:  # pragma: no cover - defensive
            continue
        start = starts[keyword.lineno - 1] + keyword.col_offset
        edits.append(
            (
                start,
                start + len(keyword.arg),
                _DEAL_PREDICATE_KEYWORD.encode(),
            )
        )


def _span(node: ast.expr, starts: Sequence[int]) -> tuple[int, int] | None:
    """The source offsets of a node that lies on one line, or ``None``."""

    if node.end_lineno is None or node.end_col_offset is None:
        return None  # pragma: no cover - positions are set by ast.parse
    if node.lineno != node.end_lineno:
        return None
    line = starts[node.lineno - 1]
    return line + node.col_offset, line + node.end_col_offset


def _line_starts(data: bytes) -> list[int]:
    starts = [0]
    for line in data.split(b"\n"):
        starts.append(starts[-1] + len(line) + 1)
    return starts


def _python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.rglob("*.py"))
        elif path.suffix == ".py":
            yield path
        else:
            raise LintError(f"{path}: not a Python file or directory")


def format_violations(violations: Sequence[Violation]) -> str:
    return "\n".join(violation.format() for violation in violations)
