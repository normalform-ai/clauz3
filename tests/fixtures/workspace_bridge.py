"""Run the real bridge and expose its worker PID to lifecycle regression tests."""

import asyncio
import os
import runpy
from pathlib import Path
from typing import Any
from unittest.mock import patch

launch = asyncio.create_subprocess_exec


async def observed(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
    process = await launch(*args, **kwargs)
    if "clauz3.sessions.worker" in args:
        Path(os.environ["WORKSPACE_TEST_WORKER_PID"]).write_text(str(process.pid))
    return process


if __name__ == "__main__":
    with patch("asyncio.create_subprocess_exec", observed):
        runpy.run_module("clauz3.sessions.tools", run_name="__main__")
