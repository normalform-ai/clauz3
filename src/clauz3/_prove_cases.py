"""Prove a batch of named proof fixtures and check each against its name.

Internal helper for the example and bundled-library Justfiles; not part of
the ``clauz3`` command line. Run as::

    python -m clauz3._prove_cases [prove options] CASE [CASE ...]

It takes the same options as ``clauz3 prove`` and proves every case in one
process, each on its own, exactly as ``clauz3 prove`` would. The expected
outcome comes from the file name:

- ``*_pass.py`` must prove (``clauz3 prove`` would exit 0);
- ``*_fail.py`` must not (it would exit non-zero, for an unproved obligation
  or a configuration error alike).

Exits 0 when every case matches its expectation and 1 when any does not.
Exits 2, before proving anything, when a case's name has neither suffix or
its path does not exist. A missing path is refused rather than counted as a
``_fail`` case, so a typo or a glob that matched nothing (the shell passes
``cases/*_fail.py`` through literally) cannot pass the batch.

Unlike the old ``if clauz3 prove x_fail.py; then exit 1; fi`` idiom, a
``_fail`` case only counts as failing when ``clauz3 prove`` reports a
failure: an unproved obligation or a configuration error. An unexpected
exception from the prover is not a failure; it propagates and aborts the
batch, leaving the remaining cases unreported.
"""

import argparse
import sys
from pathlib import Path

from clauz3.cli import _add_prove_args, _prove_entry
from clauz3.libraries.layout import LayoutError

PASS_SUFFIX = "_pass.py"
FAIL_SUFFIX = "_fail.py"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m clauz3._prove_cases",
        description="Prove named proof fixtures and check each against its name.",
    )
    parser.add_argument("cases", type=Path, nargs="+", metavar="CASE")
    _add_prove_args(parser)
    args = parser.parse_args(argv)

    cases: list[Path] = args.cases
    refusals = [
        f"{case}: name must end in {PASS_SUFFIX} or {FAIL_SUFFIX}"
        for case in cases
        if not case.name.endswith((PASS_SUFFIX, FAIL_SUFFIX))
    ] + [
        f"{case}: no such file (a typo, or a glob that matched nothing?)"
        for case in cases
        if not case.is_file()
    ]
    if refusals:
        for refusal in refusals:
            print(f"error: {refusal}", file=sys.stderr)
        return 2

    unexpected: list[str] = []
    for case in cases:
        expect_proved = case.name.endswith(PASS_SUFFIX)
        try:
            status = _prove_entry(case, args, label=True)
        except LayoutError as exc:
            print(f"error: {case}: {exc}", file=sys.stderr)
            status = 2
        if (status == 0) != expect_proved:
            unexpected.append(
                f"{case}: expected it to {'prove' if expect_proved else 'fail'}"
            )

    if unexpected:
        print(f"{len(unexpected)} of {len(cases)} cases did not match their names:")
        for line in unexpected:
            print(f"  {line}")
        return 1
    print(f"all {len(cases)} cases matched their names")
    return 0


if __name__ == "__main__":
    sys.exit(main())
