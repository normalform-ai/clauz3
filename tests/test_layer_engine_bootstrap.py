"""Cold CLI and prover startup capture their shared engine dependencies."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIND = ROOT / "src/clauz3/stdlib/find/tests"


def _cold_process(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", script, *arguments],
        cwd=FIND,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_cold_cli_and_prover_capture_the_same_stdlib_engine_closure() -> None:
    script = """
import sys

if sys.argv[1] == "cli":
    import clauz3.cli
else:
    import clauz3.prover

from clauz3.layer_state import _ENGINE_MODULES
import json

print(json.dumps({
    name: {
        "origin": None if engine.origin is None else str(engine.origin),
        "kind": engine.plan.components[-1].kind,
    }
    for name, engine in _ENGINE_MODULES.items()
    if name.split(".")[0] in sys.stdlib_module_names
}, sort_keys=True))
"""
    snapshots = []
    for entrypoint in ("cli", "prover"):
        result = _cold_process(script, entrypoint)
        assert result.returncode == 0, result.stdout + result.stderr
        snapshots.append(json.loads(result.stdout))

    cli, prover = snapshots
    assert {
        "argparse",
        "asyncio",
        "decimal",
        "secrets",
        "http.server",
        "urllib.request",
        "subprocess",
        "signal",
    } <= prover.keys()
    assert prover == cli


def test_cold_cli_proves_bundled_subprocess_layer() -> None:
    result = _cold_process(
        "import sys\n"
        "assert 'signal' not in sys.modules\n"
        "from clauz3.cli import main\n"
        "raise SystemExit(main(['prove', '--trusted-root', '../tools/find/trusted', "
        "'--import-root', '..', '--import-root', '../../filesystem', "
        "'cases/only_read_under_pass.py']))\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "main: proved!" in result.stdout


@pytest.mark.parametrize("replacement", ["cached", "selected-shadow"])
def test_cold_engine_capture_rejects_later_signal_replacements(
    tmp_path: Path, replacement: str
) -> None:
    layer = tmp_path / "bootstrap_tools"
    layer.mkdir()
    (layer / "__init__.py").write_text("")
    (layer / "effects.py").write_text("import signal\n")
    if replacement == "selected-shadow":
        (tmp_path / "signal.py").write_text(
            "raise AssertionError('shadow initializer executed')\n"
        )
    expected = (
        "cached engine dependency identity changed: signal"
        if replacement == "cached"
        else "cannot replace an initialized engine module: signal"
    )
    result = _cold_process(
        "import sys\n"
        "assert 'signal' not in sys.modules\n"
        "import clauz3.cli\n"
        "from pathlib import Path\n"
        "from types import ModuleType\n"
        "from clauz3.prover import ProverConfigError, prove_text\n"
        "original = sys.modules['signal']\n"
        "if sys.argv[1] == 'cached':\n"
        "    replacement = ModuleType('signal')\n"
        "    replacement.__dict__.update(vars(original))\n"
        "    sys.modules['signal'] = replacement\n"
        "try:\n"
        "    prove_text('def main():\\n    assert True\\n', "
        "trusted_roots=[Path(sys.argv[2])], import_paths=[Path(sys.argv[2]).parent])\n"
        "except ProverConfigError as exc:\n"
        "    assert str(exc) == sys.argv[3], str(exc)\n"
        "else:\n"
        "    raise AssertionError('replacement was admitted')\n",
        replacement,
        str(layer),
        expected,
    )
    assert result.returncode == 0, result.stdout + result.stderr
