# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import write_file

import clauz3


@clauz3.guarantee(fs.only_write_under("/sandbox"))
def main(use_logs: bool) -> None:
    if use_logs:
        write_file("/sandbox/logs/run.txt", "log")
    else:
        write_file("/sandbox/out/result.txt", "result")
