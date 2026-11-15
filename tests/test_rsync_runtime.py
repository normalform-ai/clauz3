"""The rsync trusted boundary refuses options that delete or reach other paths."""

import importlib.util
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

RSYNC_ROOT = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib/rsync"

RsyncEffect = Callable[[str, str, bool, list[str]], str]


@pytest.fixture
def rsync_effect(monkeypatch: pytest.MonkeyPatch) -> RsyncEffect:
    """The bundled rsync effect, loaded by path to run its body."""
    monkeypatch.syspath_prepend(str(RSYNC_ROOT))
    spec = importlib.util.spec_from_file_location(
        "rsync_effects", RSYNC_ROOT / "tools/rsync/trusted/effects.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(RsyncEffect, module.rsync)


@pytest.mark.parametrize(
    "args",
    [
        ["--delete"],
        ["--del"],
        ["--delete-after"],
        ["--delete-excluded"],
        ["--dele"],  # getopt_long accepts unambiguous abbreviations
        ["--remove-source-files"],
        ["--remove-sou"],
        ["--remove-sent-files"],
        ["--backup-dir=/elsewhere"],
        ["--backup-dir", "/elsewhere"],
        ["--log-file=/tmp/log"],
        ["--write-batch=/tmp/batch"],
        ["--only-write-batch=/tmp/batch"],
        ["--read-batch=/tmp/batch"],
        ["--partial-dir=/tmp/partial"],
        ["--temp-dir=/tmp"],
        ["-T", "/tmp"],
        ["-aT/tmp"],
        ["--compare-dest=/other"],
        ["--copy-dest=/other"],
        ["--link-dest=/other"],
        ["--files-from=/tmp/list"],
        ["--early-input=/tmp/secret"],
        ["-e", "sh -c 'rm -rf ~'"],
        ["-avze", "ssh"],
        ["--rsh=ssh"],
        ["--rsync-path=/bin/sh"],
        ["-M--delete"],
        ["--remote-option=--delete"],
        ["--daemon"],
        ["--server"],
        ["--sender"],
        ["--force"],
        ["--for"],
        ["--keep-dirlinks"],
        ["--keep-d"],
        ["-K"],
        ["-avK"],
    ],
)
@pytest.mark.usefixtures("deal_disabled")
def test_rsync_refuses_unsafe_options_before_subprocess(
    rsync_effect: RsyncEffect, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("a refused rsync option reached the subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="rsync option"):
        rsync_effect("/repo/build/", "/backup/build/", False, args)


@pytest.mark.parametrize(
    "args",
    [
        ["-a"],
        ["-avz"],
        ["--partial"],
        ["--backup", "--suffix=.bak"],
        ["-b"],
        ["--exclude", "*.tmp"],
        ["--checksum", "--dry-run"],
        ["--delay-updates"],
    ],
)
def test_rsync_passes_safe_options(
    rsync_effect: RsyncEffect, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    calls: list[list[str]] = []

    def record(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", record)
    assert rsync_effect("/repo/build/", "/backup/build/", False, args) == "ok"
    assert calls == [["rsync", *args, "/repo/build/", "/backup/build/"]]


@pytest.mark.usefixtures("deal_disabled")
def test_rsync_body_refuses_without_deal(
    rsync_effect: RsyncEffect, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("a refused rsync call reached the subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ValueError):
        rsync_effect("", "/backup/build/", False, ["-a"])


def test_precondition_names_every_refused_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(RSYNC_ROOT))
    spec = importlib.util.spec_from_file_location(
        "rsync_effects_names", RSYNC_ROOT / "tools/rsync/trusted/effects.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = [*module.REFUSED_LONG_OPTIONS, "--del"]
    names += [f"-{letter}" for letter in module.REFUSED_SHORT_OPTIONS]
    for name in names:
        assert not module.has_no_refused_option_names([name]), name
