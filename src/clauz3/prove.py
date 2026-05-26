import sys

from clauz3.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    return cli_main(["prove", *(argv or [])])


if __name__ == "__main__":
    raise SystemExit(main())
