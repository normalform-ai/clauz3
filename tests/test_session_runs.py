"""Owned workers have bounded proof time and independently observable status."""

import asyncio
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from clauz3.approvals.client import ApprovalServiceError
from clauz3.diagnostics import proof
from clauz3.sessions import runs as module
from clauz3.sessions.runs import Runs


async def ignore(_record: dict[str, object]) -> None:
    pass


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    original = asyncio.create_subprocess_exec

    def install(body: str) -> None:
        async def launch(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
            assert args[:4] == (
                sys.executable,
                "-P",
                "-m",
                "clauz3.sessions.worker",
            )
            prefix = (
                "import os, sys, time, json, subprocess\n"
                "fd, deadline, gate = int(sys.argv[1]), float(sys.argv[2]), "
                "int(sys.argv[3])\n"
                "def emit(**event):\n"
                "    os.write(fd, (json.dumps(event) + '\\n').encode())\n"
            )
            return await original(
                sys.executable, "-c", prefix + body, *args[4:], **kwargs
            )

        monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)

    return install


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "bad"])
def test_invalid_operator_budget(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(module.PROOF_TIMEOUT_ENV, value)
    with pytest.raises(ValueError, match=module.PROOF_TIMEOUT_ENV):
        module.proof_timeout()


def test_deadline_covers_construction_and_kills_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: Callable[[str], None]
) -> None:
    monkeypatch.setenv(module.PROOF_TIMEOUT_ENV, "2")
    worker(
        "subprocess.Popen([sys.executable, '-c', "
        '"import time; from pathlib import Path; time.sleep(3.5); '
        "Path('escaped').touch()\"])\n"
        "emit(phase='proving', description='constructing proof obligations')\n"
        "time.sleep(10)\n"
    )

    async def inspect() -> None:
        runs = Runs(tmp_path, [])
        start = time.monotonic()
        result = await asyncio.wait_for(runs.run("", ignore), 5)
        assert time.monotonic() - start < 4
        assert "approval was not requested" in result
        assert "constructing proof obligations" in result
        assert runs.status()[0]["phase"] == "refused"
        await asyncio.sleep(2)
        assert not (tmp_path / "escaped").exists()

    asyncio.run(inspect())


def test_stdout_cannot_spoof_progress_or_disable_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: Callable[[str], None]
) -> None:
    monkeypatch.setenv(module.PROOF_TIMEOUT_ENV, "0.3")
    worker("print(json.dumps({'phase': 'executing'}), flush=True)\ntime.sleep(10)\n")

    async def inspect() -> None:
        runs = Runs(tmp_path, [])
        result = await asyncio.wait_for(runs.run("", ignore), 5)
        assert "Total proof deadline" in result
        assert runs.status()[0]["phase"] == "refused"

    asyncio.run(inspect())


def test_slow_notifications_do_not_block_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: Callable[[str], None]
) -> None:
    monkeypatch.setenv(module.PROOF_TIMEOUT_ENV, "0.3")
    worker("emit(phase='proving', obligation=1)\ntime.sleep(10)\n")

    async def inspect() -> None:
        async def stalled(_record: dict[str, object]) -> None:
            await asyncio.sleep(10)

        result = await asyncio.wait_for(Runs(tmp_path, []).run("", stalled), 3)
        assert "Total proof deadline" in result

    asyncio.run(inspect())


def test_active_status_and_cancellation(
    tmp_path: Path, worker: Callable[[str], None]
) -> None:
    worker("emit(phase='proving', line=42, predicate='safe(path)')\ntime.sleep(10)\n")

    async def inspect() -> None:
        runs = Runs(tmp_path, [])
        task = asyncio.create_task(runs.run("", ignore))
        for _ in range(100):
            if runs.status() and runs.status()[0].get("line") == 42:
                break
            await asyncio.sleep(0.02)
        status = runs.status()[0]
        assert status["phase"] == "proving"
        assert status["line"] == 42
        await asyncio.sleep(0.03)
        assert runs.status()[0]["elapsed_seconds"] != status["elapsed_seconds"]
        assert "still active" in await runs.run("", ignore)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert runs.status()[0]["phase"] == "cancelled"

    asyncio.run(inspect())


def test_pending_requires_service_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: Callable[[str], None]
) -> None:
    monkeypatch.setenv(module.PROOF_TIMEOUT_ENV, "2")
    monkeypatch.setenv("CLAUZ3_APPROVAL_SERVICE", "http://approval.test")
    confirmed = False
    lookups = 0

    def service_url(**kwargs: object) -> str:
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            raise ApprovalServiceError("temporarily invalid configuration")
        return "http://approval.test"

    monkeypatch.setattr(module, "configured_service_url", service_url)

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def read(self, _limit: int) -> bytes:
            if not confirmed:
                return b"{}"
            return json.dumps({"request_id": "test", "status": "pending"}).encode()

    monkeypatch.setattr(module, "urlopen", lambda *_args, **_kw: Response())
    worker(
        "emit(phase='submitting_approval', request_id='test')\n"
        "assert os.read(gate, 1) == b'1'\n"
        "time.sleep(10)\n"
    )

    async def inspect() -> None:
        nonlocal confirmed
        runs = Runs(tmp_path, [])
        task = asyncio.create_task(runs.run("", ignore))
        try:
            await asyncio.sleep(2.3)
            assert runs.status()[0]["phase"] == "submitting_approval"
            assert not task.done()  # Approval waits do not consume the proof budget.
            assert lookups >= 2  # A lookup error must not kill the poller.
            confirmed = True
            for _ in range(100):
                if runs.status()[0]["phase"] == "awaiting_approval":
                    break
                await asyncio.sleep(0.02)
            assert runs.status()[0]["phase"] == "awaiting_approval"
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(inspect())


def test_solver_budget_is_clamped_and_monitor_is_scoped() -> None:
    assert proof.solver_timeout_ms(5000) == 5000
    with proof.monitoring(proof.Monitor(time.monotonic() + 0.1, lambda _: None)):
        assert 0 < proof.solver_timeout_ms(5000) <= 100
    assert not proof.active()
    with (
        proof.monitoring(proof.Monitor(time.monotonic() - 1, lambda _: None)),
        pytest.raises(proof.ProofDeadlineExceeded),
    ):
        proof.solver_timeout_ms(5000)


def test_interactive_proof_stops_at_first_unknown_and_labels_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clauz3._vendor.deal_solver._theorem import Conclusion, Proof, Theorem
    from clauz3.prover import prove_text

    package = tmp_path / "test_progress_tools"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\nimport deal\n"
        "@clauz3.trusted\n@deal.pre(lambda x: x > 0)\n"
        "def send(x: int) -> None:\n    pass\n"
    )
    source = (
        "from test_progress_tools.effects import send\n"
        "def main():\n"
        "    for i in range(5):\n"
        "        if i >= 0:\n"
        "            send(i + 1)\n"
    )
    checks = 0

    def unresolved(*, solver: Any, descr: str, timeout: float) -> Proof:
        nonlocal checks
        checks += 1
        return Proof(Conclusion.SKIP, descr, error=TimeoutError("timeout"))

    monkeypatch.setattr(Theorem, "_prove", staticmethod(unresolved))
    # This tests early stopping and metadata, not construction speed. Keep the
    # monitor's clock fixed without changing the clock used by other modules.
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=lambda: 0.0))
    events: list[dict[str, object]] = []
    with proof.monitoring(proof.Monitor(10.0, events.append)):
        results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert checks == 1
    assert results[0].proof.conclusion == Conclusion.SKIP
    assert events[0]["line"] == 5
    assert events[0]["call"] == "send(i + 1)"
    assert events[0]["effect"] == "test_progress_tools.effects.send"
    assert events[0]["predicate"] == "lambda x: x > 0"
    assert events[-1]["conclusion"] == "skipped"
    # Standalone proofs retain their existing aggregate diagnostic behavior.
    checks = 0
    prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert checks == 5


def test_worker_stops_when_bridge_disconnects(tmp_path: Path) -> None:
    import os
    import signal
    import subprocess

    progress_read, progress_write = os.pipe()
    gate_read, gate_write = os.pipe()
    life_read, life_write = os.pipe()
    ready = tmp_path / "ready"
    script = (
        "import time\nfrom pathlib import Path\n"
        "from clauz3.sessions import worker\n"
        f"def work(args):\n    Path({str(ready)!r}).touch()\n    time.sleep(30)\n"
        "worker.main = work\nworker.run()\n"
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-P",
            "-c",
            script,
            str(progress_write),
            str(time.monotonic() + 10),
            str(gate_read),
            str(life_read),
        ],
        pass_fds=(progress_write, gate_read, life_read),
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        limit = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < limit:
            time.sleep(0.02)
        assert ready.exists()
        os.close(life_write)
        life_write = -1
        assert child.wait(timeout=3) == -signal.SIGKILL
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=3)
        for fd in (
            progress_read,
            progress_write,
            gate_read,
            gate_write,
            life_read,
            life_write,
        ):
            if fd != -1:
                os.close(fd)


def test_obligation_locations_are_scoped_to_context_and_keep_first_site() -> None:
    import z3

    first = z3.Bool("same", ctx=z3.Context())
    second = z3.Bool("same", ctx=z3.Context())
    assert first.get_id() == second.get_id()
    with proof.monitoring(proof.Monitor(time.monotonic() + 10, lambda _: None)):
        proof.remember(first, line=1)
        proof.remember(second, line=2)
        proof.remember(z3.Bool("same", ctx=first.ctx), line=3)
        assert proof.details(first) == {"line": 1}
        assert proof.details(second) == {"line": 2}


@pytest.mark.parametrize("expiry", ["worker", "supervisor"])
def test_real_worker_timeout_retains_obligation_without_requesting_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expiry: str
) -> None:
    from clauz3.approvals.client import APPROVAL_SERVICE_ENV, MockApprovalServer

    package = tmp_path / "timeout_tools"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\nimport deal\n"
        "@clauz3.trusted\n@deal.pre(lambda x: x > 0)\n"
        "def send(x: int) -> None:\n    pass\n"
    )
    source = "from timeout_tools.effects import send\ndef main():\n    send(1)\n"
    original = asyncio.create_subprocess_exec
    # Keep the actual worker, CLI, prover, and private progress/gate pipes.
    # Control only the solver timing so this regression needs no hard Z3 puzzle.
    action = (
        "proof._CURRENT.get().deadline = time.monotonic() - 1\n"
        "    proof.solver_timeout_ms(5000)\n"
        if expiry == "worker"
        else "time.sleep(60)\n"
    )
    script = (
        "import time\n"
        "from clauz3._vendor.deal_solver._theorem import Theorem\n"
        "from clauz3.diagnostics import proof\n"
        "from clauz3.sessions.worker import run\n"
        "def expire(**kwargs):\n    "
        + action
        + "Theorem._prove = staticmethod(expire)\n"
        "raise SystemExit(run())\n"
    )

    async def launch(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        return await original(sys.executable, "-P", "-c", script, *args[4:], **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    monkeypatch.setenv(module.PROOF_TIMEOUT_ENV, "15")
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "cache"))
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)

        async def inspect() -> None:
            runs = Runs(tmp_path, [package])
            result = await asyncio.wait_for(runs.run(source, ignore), 25)
            assert "approval was not requested" in result
            status = runs.status()[0]
            assert status["phase"] == "refused"
            assert status["line"] == 3
            assert status["call"] == "send(1)"
            assert status["predicate"] == "lambda x: x > 0"
            assert status["exit_code"] == 2
            if expiry == "worker":
                assert "proof timeout:" in result

        asyncio.run(inspect())
        assert server.requests == []


@pytest.mark.parametrize("expiry", [True, False])
def test_truncated_progress_preserves_deadline_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker: Callable[[str], None],
    expiry: bool,
) -> None:
    monkeypatch.setenv(module.PROOF_TIMEOUT_ENV, "2")
    worker(
        "emit(phase='proving', line=7, predicate='safe(path)')\n"
        'os.write(fd, b\'{"phase": "proving"\')\n'
        + ("time.sleep(10)\n" if expiry else "")
    )

    async def inspect() -> None:
        runs = Runs(tmp_path, [])
        if expiry:
            result = await asyncio.wait_for(runs.run("", ignore), 5)
            assert "approval was not requested" in result
            assert runs.status()[0]["phase"] == "refused"
            assert runs.status()[0]["line"] == 7
        else:
            with pytest.raises(ValueError, match="truncated worker progress"):
                await asyncio.wait_for(runs.run("", ignore), 5)
            assert runs.status()[0]["phase"] == "failed"

    asyncio.run(inspect())


def test_advisory_snapshot_failure_does_not_abort_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: Callable[[str], None]
) -> None:
    worker(
        "emit(phase='submitting_approval')\n"
        "os.read(gate, 1)\n"
        "emit(phase='executing')\n"
        "time.sleep(0.1)\n"
        "open('effect-completed', 'w').close()\n"
    )
    status = tmp_path / "runs.json"
    write = Path.write_text

    def fail(path: Path, *args: Any, **kwargs: Any) -> int:
        if path == status.with_suffix(".tmp"):
            raise OSError("status disk unavailable")
        return write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail)

    async def check() -> None:
        runs = Runs(tmp_path, [], status_file=status)
        await asyncio.wait_for(runs.run("", ignore), 5)
        assert runs.status()[0]["phase"] == "completed"
        assert (tmp_path / "effect-completed").exists()

    asyncio.run(check())
