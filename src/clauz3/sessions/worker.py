"""Owned run worker: progress uses a private FD, never program stdout."""

import json
import os
import signal
import sys
import threading

from clauz3.cli import main
from clauz3.diagnostics.proof import Monitor, ProofDeadlineExceeded, monitoring


def run() -> int:
    descriptor = int(sys.argv[1])
    deadline = float(sys.argv[2])
    gate = int(sys.argv[3])
    os.set_inheritable(gate, False)
    lifetime = int(sys.argv[4])
    os.set_inheritable(lifetime, False)

    def stop_on_disconnect() -> None:
        # The bridge owns the only write end. Even abrupt bridge termination
        # must stop this worker group, which is separate for call cancellation.
        os.read(lifetime, 1)
        os.killpg(os.getpgrp(), signal.SIGKILL)

    threading.Thread(target=stop_on_disconnect, daemon=True).start()
    # Trusted effects may launch subprocesses during execution. Those children
    # must not keep the private progress channel open after the worker exits.
    os.set_inheritable(descriptor, False)
    with os.fdopen(descriptor, "w", buffering=1) as stream:

        def emit(event: dict[str, object]) -> None:
            stream.write(json.dumps(event) + "\n")
            # Only the supervising parent can release the proof deadline.
            # No HTTP request is made if it times out or disconnects.
            if event.get("phase") == "submitting_approval" and os.read(gate, 1) != b"1":
                raise ProofDeadlineExceeded("proof supervisor disconnected")

        with monitoring(Monitor(deadline=deadline, emit=emit)):
            return main(sys.argv[5:])


if __name__ == "__main__":
    raise SystemExit(run())
