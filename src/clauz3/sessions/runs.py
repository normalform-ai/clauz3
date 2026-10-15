"""Supervise owned workers and retain bounded, read-only run status."""

import asyncio
import json
import math
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen
from uuid import uuid4

from clauz3.approvals.client import ApprovalServiceError, configured_service_url
from clauz3.libraries.layout import expand_import_roots

PROOF_TIMEOUT_ENV = "CLAUZ3_PROOF_TIMEOUT_SECONDS"
DEFAULT_PROOF_TIMEOUT = 30.0
STATUS_FILE_ENV = "CLAUZ3_RUN_STATUS_FILE"
_TERMINAL = {"completed", "refused", "failed", "cancelled"}


def proof_timeout() -> float:
    try:
        value = float(os.environ.get(PROOF_TIMEOUT_ENV, str(DEFAULT_PROOF_TIMEOUT)))
    except ValueError as exc:
        raise ValueError(
            f"{PROOF_TIMEOUT_ENV} must be a finite positive number"
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{PROOF_TIMEOUT_ENV} must be a finite positive number")
    return value


class Runs:
    def __init__(
        self, cwd: Path, roots: list[Path], *, status_file: Path | None = None
    ) -> None:
        self.cwd = cwd
        self.roots = roots
        self.timeout = proof_timeout()
        self.records: dict[str, dict[str, object]] = {}
        self.started: dict[str, float] = {}
        self.status_file = status_file

    def status(self) -> list[dict[str, object]]:
        result = []
        for run_id, record in self.records.items():
            copy = dict(record)
            if record["phase"] not in _TERMINAL:
                copy["elapsed_seconds"] = round(
                    time.monotonic() - self.started[run_id], 3
                )
            result.append(copy)
        return result

    async def run(
        self, source: str, notify: Callable[[dict[str, object]], Awaitable[None]]
    ) -> str:
        # Bound worker count, including accidental concurrent retries.
        if any(r["phase"] not in _TERMINAL for r in self.records.values()):
            return (
                "exit code: 2\nA run is still active. "
                "Use status to inspect it before retrying."
            )
        while len(self.records) >= 20:
            oldest = next(iter(self.records))
            del self.records[oldest], self.started[oldest]
        run_id = str(uuid4())
        started = time.monotonic()
        deadline = started + self.timeout
        record: dict[str, object] = {
            "run_id": run_id,
            "phase": "checking",
            "proof_timeout_seconds": self.timeout,
            "elapsed_seconds": 0.0,
            "source_sha256": sha256(source.encode()).hexdigest(),
        }
        self.records[run_id] = record
        self.started[run_id] = started
        finished_proving = asyncio.Event()
        timed_out = False
        execution_started = False
        process: asyncio.subprocess.Process | None = None
        tasks: list[asyncio.Task[None]] = []
        transport: asyncio.ReadTransport | None = None
        read_fd, write_fd = os.pipe()
        gate_read, gate_write = os.pipe()
        lifetime_read, lifetime_write = os.pipe()
        notifications: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=1)

        def update(event: dict[str, object]) -> None:
            if event.get("phase") == "proving" and "description" in event:
                for key in (
                    "line",
                    "column",
                    "call",
                    "effect",
                    "predicate",
                    "conclusion",
                    "result",
                    "source_kind",
                    "module",
                ):
                    record.pop(key, None)
            record.update(event)
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            if self.status_file is not None:
                # The launcher owns this path in a private temporary directory.
                # Atomic replacement lets the UI observe real worker progress
                # independently of ACP's optional MCP progress forwarding.
                # Display failures must not interrupt approved execution.
                with suppress(OSError):
                    temporary = self.status_file.with_suffix(".tmp")
                    temporary.write_text(json.dumps(self.status()))
                    temporary.replace(self.status_file)
            # A slow/disconnected MCP consumer must not block supervision.
            if notifications.full():
                notifications.get_nowait()
            notifications.put_nowait(dict(record))

        async def send_notifications() -> None:
            while True:
                event = await notifications.get()
                with suppress(Exception):
                    async with asyncio.timeout(1):
                        await notify(event)

        async def read_progress(reader: asyncio.StreamReader) -> None:
            nonlocal execution_started
            while line := await reader.readline():
                if not line.endswith(b"\n"):
                    if timed_out:
                        return  # SIGKILL may interrupt the worker's final write.
                    raise ValueError("truncated worker progress message")
                event = json.loads(line)
                phase = event.get("phase")
                if phase not in {
                    "checking",
                    "proving",
                    "submitting_approval",
                    "executing",
                }:
                    raise ValueError("invalid worker progress phase")
                if phase == "submitting_approval":
                    if timed_out or time.monotonic() >= deadline:
                        continue
                    # This synchronous transition wins or loses against the
                    # watchdog as one event-loop operation. The worker cannot
                    # POST until it receives this acknowledgement.
                    finished_proving.set()
                    os.write(gate_write, b"1")
                elif phase == "executing":
                    if not finished_proving.is_set():
                        raise ValueError("execution reported before proof completion")
                    execution_started = True
                update(event)

        def pending(request_id: str) -> bool:
            try:
                service = configured_service_url(cwd=self.cwd)
                if service is None:
                    return False
                with urlopen(
                    f"{service}/requests/{quote(request_id, safe='')}", timeout=1
                ) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    return False
                payload = json.loads(raw)
                return (
                    isinstance(payload, dict)
                    and payload.get("request_id") == request_id
                    and payload.get("status") == "pending"
                )
            except (ApprovalServiceError, URLError, OSError, ValueError):
                return False

        async def poll_approval() -> None:
            while True:
                if record.get("phase") == "submitting_approval":
                    request_id = str(record.get("request_id", ""))
                    is_pending = bool(request_id) and await asyncio.to_thread(
                        pending, request_id
                    )
                    if is_pending and record.get("phase") == "submitting_approval":
                        update({"phase": "awaiting_approval"})
                await asyncio.sleep(0.25)

        async def deadline_watch() -> None:
            nonlocal timed_out
            try:
                async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
                    await finished_proving.wait()
            except TimeoutError:
                if not finished_proving.is_set():
                    timed_out = True
                    if process is not None:
                        _kill(process)

        try:
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: protocol, os.fdopen(read_fd, "rb")
            )
            read_fd = -1
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-P",
                "-m",
                "clauz3.sessions.worker",
                str(write_fd),
                str(deadline),
                str(gate_read),
                str(lifetime_read),
                "run",
                "-",
                # The same ordered roots the approval service authorized.
                *[
                    arg
                    for root in expand_import_roots([self.cwd])
                    for arg in ("--import-root", str(root))
                ],
                *[arg for root in self.roots for arg in ("--trusted-root", str(root))],
                cwd=self.cwd,
                pass_fds=(write_fd, gate_read, lifetime_read),
                start_new_session=True,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            os.close(write_fd)
            os.close(gate_read)
            os.close(lifetime_read)
            write_fd = gate_read = lifetime_read = -1
            update({"phase": "checking"})
            reader_task = asyncio.create_task(read_progress(reader))
            tasks = [
                reader_task,
                asyncio.create_task(poll_approval()),
                asyncio.create_task(deadline_watch()),
                asyncio.create_task(send_notifications()),
            ]
            output_task = asyncio.create_task(process.communicate(source.encode()))
            try:
                # Observe a broken progress channel immediately, even during
                # approval waits. EOF itself is normal when the worker exits.
                await asyncio.wait(
                    (output_task, reader_task), return_when=asyncio.FIRST_COMPLETED
                )
                if reader_task.done():
                    await reader_task
                output, _ = await output_task
                await reader_task
            finally:
                output_task.cancel()
                with suppress(asyncio.CancelledError):
                    await output_task
            if timed_out:
                message = (
                    f"Total proof deadline ({self.timeout:g}s) exceeded; "
                    "approval was not requested and the program did not execute."
                )
                update({"phase": "refused", "reason": message, "exit_code": 2})
                return (
                    f"run id: {run_id}\nexit code: 2\n{message}\n"
                    f"Last obligation: {json.dumps(record)}"
                )
            code = process.returncode
            phase = (
                "completed"
                if code == 0
                else ("failed" if execution_started else "refused")
            )
            update({"phase": phase, "exit_code": code})
            return (
                f"run id: {run_id}\nphase: {phase}\nexit code: {code}\n"
                f"{output.decode(errors='replace')}\nStatus: {json.dumps(record)}"
            )
        except asyncio.CancelledError:
            update({"phase": "cancelled"})
            raise
        except Exception as exc:
            update({"phase": "failed", "reason": str(exc)})
            raise
        finally:
            if process is not None:
                _kill(process)
                await process.wait()
            for task in tasks:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            if transport is not None:
                transport.close()
            for fd in (
                read_fd,
                write_fd,
                gate_read,
                gate_write,
                lifetime_read,
                lifetime_write,
            ):
                if fd != -1:
                    os.close(fd)


def _kill(process: asyncio.subprocess.Process) -> None:
    # Include children created by a trusted effect. Use a private process group
    # so cancellation cannot signal the agent harness or approval service.
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
