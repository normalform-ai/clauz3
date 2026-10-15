"""Keep the ACP process group tied to the workspace service's lifetime."""

import os
import signal
import subprocess
import sys
import threading


def main() -> int:
    descriptor = int(sys.argv[1])
    os.set_inheritable(descriptor, False)

    def disconnected() -> None:
        os.read(descriptor, 1)
        os.killpg(os.getpgrp(), signal.SIGKILL)

    threading.Thread(target=disconnected, daemon=True).start()
    process = subprocess.Popen(sys.argv[2:])
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
