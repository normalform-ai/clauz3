#!/usr/bin/env python3
"""Manually snapshot ClauZ3's GitHub aggregates; see github_download_counts.md."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY = "normalform-ai/clauz3"
OUTPUT_DIR = Path.home() / ".local/share/clauz3-ipo"


def get(endpoint: str, *, paginate: bool = False) -> Any:
    """Use gh's Link-header pagination, including separate asset-list pages."""
    command = [
        "gh",
        "api",
        "--hostname",
        "github.com",
        "--method",
        "GET",
        "-H",
        "Accept: application/vnd.github+json",
        "-H",
        "X-GitHub-Api-Version: 2022-11-28",
        endpoint,
    ]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=120, check=False
        )
    except FileNotFoundError as exc:
        raise ValueError("gh is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("GitHub request timed out") from exc
    except OSError as exc:
        raise ValueError("Could not execute gh") from exc
    if result.returncode:
        # Do not persist raw stderr: it may contain account or credential details.
        status = re.search(r"HTTP (\d{3})", result.stderr)
        detail = f"HTTP {status[1]}" if status else f"gh exit {result.returncode}"
        raise ValueError(f"GitHub request failed ({detail})")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("GitHub returned invalid JSON") from exc


def records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise ValueError("Expected a list of GitHub records")
    return value


def pages(endpoint: str) -> list[dict[str, Any]]:
    value = get(endpoint + "?per_page=100", paginate=True)
    if not isinstance(value, list) or not value:
        raise ValueError("Expected paginated GitHub response")
    return [record for page in value for record in records(page)]


def number(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Missing or invalid nonnegative GitHub count/ID")
    return value


def release_counts() -> dict[str, Any]:
    assets = []
    release_ids: set[int] = set()
    asset_ids: set[int] = set()
    for release in pages(f"repos/{REPOSITORY}/releases"):
        release_id = number(release.get("id"))
        if release_id in release_ids:
            raise ValueError("Repeated release ID; pagination may have changed")
        release_ids.add(release_id)
        # The embedded assets list is not used: it may be incomplete.
        for asset in pages(f"repos/{REPOSITORY}/releases/{release_id}/assets"):
            asset_id = number(asset.get("id"))
            if asset_id in asset_ids:
                raise ValueError("Repeated asset ID; pagination may have changed")
            asset_ids.add(asset_id)
            assets.append(
                {
                    "release_id": release_id,
                    "asset_id": asset_id,
                    "download_count": number(asset.get("download_count")),
                }
            )
    return {
        "status": "known",
        "scope": "cumulative counts of currently visible release assets",
        "release_count": len(release_ids),
        "asset_count": len(assets),
        "download_count": sum(asset["download_count"] for asset in assets),
        "assets": assets,
    }


def clone_counts() -> dict[str, Any]:
    value = get(f"repos/{REPOSITORY}/traffic/clones?per=day")
    if not isinstance(value, dict):
        raise ValueError("Expected GitHub clone object")
    days: list[dict[str, Any]] = []
    timestamps: set[str] = set()
    for day in records(value.get("clones")):
        timestamp = day.get("timestamp")
        if not isinstance(timestamp, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T00:00:00Z", timestamp
        ):
            raise ValueError("Missing or invalid clone timestamp")
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
        if timestamp in timestamps:
            raise ValueError("Repeated clone day")
        timestamps.add(timestamp)
        days.append(
            {
                "timestamp": timestamp,
                "count": number(day.get("count")),
                "uniques": number(day.get("uniques")),
            }
        )
    count = number(value.get("count"))
    if count != sum(day["count"] for day in days):
        raise ValueError("Clone total does not match daily counts")
    return {
        "status": "known",
        "scope": "GitHub rolling last 14 days, UTC daily buckets",
        "count": count,
        "uniques": number(value.get("uniques")),
        "days": days,
    }


def unknown(reason: str) -> dict[str, Any]:
    return {"status": "unknown", "count": None, "reason": reason}


def snapshot() -> dict[str, Any]:
    result: dict[str, Any] = {
        "repository": REPOSITORY,
        "started_at": datetime.now(UTC).isoformat(),
        "source_archives": unknown("GitHub source ZIP/tar downloads not exposed"),
        "other_distribution_channels": unknown("Not measured by this GitHub script"),
        "annual_total": unknown("No complete annual distribution history"),
    }
    for key, collect in (("release_assets", release_counts), ("clones", clone_counts)):
        try:
            result[key] = collect()
        except ValueError as exc:
            # Discard partial results rather than present an incomplete sum.
            result[key] = unknown(str(exc))
    result["completed_at"] = datetime.now(UTC).isoformat()
    return result


def save(result: dict[str, Any]) -> Path:
    directory = OUTPUT_DIR
    if directory.is_symlink():
        raise ValueError("Snapshot directory must not be a symlink")
    checkout = Path(__file__).resolve().parents[1]
    if directory.resolve().is_relative_to(checkout):
        raise ValueError("Snapshots must be stored outside the checkout")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    prefix = datetime.now(UTC).strftime("snapshot-%Y%m%dT%H%M%SZ-")
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(result, stream, indent=2)
            stream.write("\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def main() -> int:
    result = snapshot()
    try:
        path = save(result)
    except (OSError, ValueError) as exc:
        print(f"Could not save snapshot: {exc}", file=sys.stderr)
        return 1
    print(path)
    failed = False
    for key in ("release_assets", "clones"):
        if result[key]["status"] == "unknown":
            print(f"{key}: unknown ({result[key]['reason']})", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
