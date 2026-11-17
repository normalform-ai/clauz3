"""The rsync trusted boundary refuses options that delete or reach other paths."""

import importlib.util
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import deal
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
        ["--password-file=/home/me/.ssh/id_rsa"],
        ["--password-file", "/home/me/.ssh/id_rsa"],
        ["--passw=/etc/shadow"],
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


@pytest.fixture
def rsync_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(RSYNC_ROOT))
    spec = importlib.util.spec_from_file_location(
        "rsync_effects_names", RSYNC_ROOT / "tools/rsync/trusted/effects.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_precondition_names_every_refused_option(rsync_module: ModuleType) -> None:
    names = [*rsync_module.REFUSED_LONG_OPTIONS, "--del"]
    names += [f"-{letter}" for letter in rsync_module.REFUSED_SHORT_OPTIONS]
    for name in names:
        assert rsync_module._refused(name), name
        assert not rsync_module.has_no_refused_option_names([name]), name


#: Every long option in the option summaries of GNU rsync 3.4 and openrsync.
#: The body refuses by prefix and the precondition by exact name, so a
#: complete option name the body refuses must be spelled out in both.
DOCUMENTED_LONG_OPTIONS = [
    "--8-bit-output",
    "--acls",
    "--address",
    "--append",
    "--append-verify",
    "--archive",
    "--atimes",
    "--backup",
    "--backup-dir",
    "--block-size",
    "--blocking-io",
    "--bwlimit",
    "--checksum",
    "--checksum-choice",
    "--checksum-seed",
    "--chmod",
    "--chown",
    "--compare-dest",
    "--compress",
    "--compress-choice",
    "--compress-level",
    "--config",
    "--contimeout",
    "--copy-as",
    "--copy-dest",
    "--copy-devices",
    "--copy-dirlinks",
    "--copy-links",
    "--copy-unsafe-links",
    "--crtimes",
    "--cvs-exclude",
    "--daemon",
    "--debug",
    "--del",
    "--delay-updates",
    "--delete",
    "--delete-after",
    "--delete-before",
    "--delete-delay",
    "--delete-during",
    "--delete-excluded",
    "--delete-missing-args",
    "--devices",
    "--dirs",
    "--dparam",
    "--dry-run",
    "--early-input",
    "--exclude",
    "--exclude-from",
    "--executability",
    "--existing",
    "--fake-super",
    "--files-from",
    "--filter",
    "--force",
    "--from0",
    "--fsync",
    "--fuzzy",
    "--group",
    "--groupmap",
    "--hard-links",
    "--help",
    "--human-readable",
    "--iconv",
    "--ignore-errors",
    "--ignore-existing",
    "--ignore-missing-args",
    "--ignore-non-existing",
    "--ignore-times",
    "--include",
    "--include-from",
    "--info",
    "--inplace",
    "--ipv4",
    "--ipv6",
    "--itemize-changes",
    "--keep-dirlinks",
    "--link-dest",
    "--links",
    "--list-only",
    "--log-file",
    "--log-file-format",
    "--log-format",
    "--max-alloc",
    "--max-delete",
    "--max-size",
    "--min-size",
    "--mkpath",
    "--modify-window",
    "--munge-links",
    "--no-detach",
    "--no-implied-dirs",
    "--no-motd",
    "--numeric-ids",
    "--old-args",
    "--old-dirs",
    "--omit-dir-times",
    "--omit-link-times",
    "--one-file-system",
    "--only-write-batch",
    "--open-noatime",
    "--out-format",
    "--outbuf",
    "--owner",
    "--partial",
    "--partial-dir",
    "--password-file",
    "--perms",
    "--port",
    "--preallocate",
    "--progress",
    "--protect-args",
    "--protocol",
    "--prune-empty-dirs",
    "--quiet",
    "--read-batch",
    "--recursive",
    "--relative",
    "--remote-option",
    "--remove-sent-files",
    "--remove-source-files",
    "--rsh",
    "--rsync-path",
    "--safe-links",
    "--secluded-args",
    "--sender",
    "--server",
    "--size-only",
    "--skip-compress",
    "--sockopts",
    "--sparse",
    "--specials",
    "--stats",
    "--stderr",
    "--stop-after",
    "--stop-at",
    "--suffix",
    "--super",
    "--temp-dir",
    "--timeout",
    "--times",
    "--trust-sender",
    "--update",
    "--usermap",
    "--verbose",
    "--version",
    "--whole-file",
    "--write-batch",
    "--write-devices",
    "--xattrs",
]


@pytest.mark.parametrize("name", DOCUMENTED_LONG_OPTIONS)
def test_precondition_agrees_with_body_on_documented_options(
    rsync_module: ModuleType, name: str
) -> None:
    """An option the body refuses by its full name is refused by the prover.

    Otherwise a program using it proves and then stops part-way through.
    """
    refused_by_precondition = not rsync_module.has_no_refused_option_names([name])
    assert rsync_module._refused(name) == refused_by_precondition


@pytest.mark.usefixtures("deal_disabled")
@pytest.mark.parametrize(
    "src,dest", [("/precious/x", "--delete"), ("--remove-source-files", "/backup/")]
)
def test_rsync_refuses_option_like_paths_without_deal(
    rsync_effect: RsyncEffect, monkeypatch: pytest.MonkeyPatch, src: str, dest: str
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("an option-like rsync path reached the subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="start with '-'"):
        rsync_effect(src, dest, False, ["-a", "/data/y"])


@pytest.mark.parametrize(
    "src,dest", [("/precious/x", "--delete"), ("--remove-source-files", "/backup/")]
)
def test_rsync_option_like_paths_fail_the_precondition(
    rsync_effect: RsyncEffect, src: str, dest: str
) -> None:
    with pytest.raises(deal.PreContractError, match="start with '-'"):
        rsync_effect(src, dest, False, ["-a"])
