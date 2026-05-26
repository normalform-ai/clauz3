"""Configure a repository for clauz3-mediated agent access.

``config`` writes the Claude Code permission settings that confine an agent to
read-only inspection plus the ``clauz3`` CLI, so every side-effecting tool call
is mediated by the prover rather than ad-hoc shell or Python execution.

It is the configuration counterpart to :mod:`clauz3.install`, which copies
trusted tool layers. The two are meant to converge on a single ``uv sync``-style
command that ensures a repo is fully set up: permissions configured and tools
installed. Re-running ``config`` is therefore idempotent — it merges the
required permissions into an existing settings file rather than clobbering it.
"""

import json
from dataclasses import dataclass
from pathlib import Path

#: Location of the Claude Code settings file within a project.
CLAUDE_SETTINGS_PATH = Path(".claude/settings.json")

#: Tools an agent may use directly. Everything with side effects must go
#: through ``clauz3``, so the allowlist is read-only inspection plus the CLI.
DEFAULT_ALLOW = ["Read", "Glob", "Grep", "Bash(clauz3:*)"]

#: Permission mode that still prompts for anything outside the allowlist.
DEFAULT_MODE = "default"


class ConfigError(Exception):
    """The destination settings file exists but cannot be updated in place."""


@dataclass(frozen=True)
class RepoConfig:
    """Result of configuring a repository."""

    path: Path
    created: bool
    added: list[str]


def configure_repo(*, into: Path, force: bool = False) -> RepoConfig:
    """Ensure ``into`` has clauz3's default Claude Code permissions.

    Writes ``.claude/settings.json`` with the read-only-plus-``clauz3``
    allowlist. If the file already exists, its entries are preserved and the
    required ones are merged in (idempotent); ``force`` overwrites it with the
    canonical defaults instead.
    """
    path = into / CLAUDE_SETTINGS_PATH

    if not path.exists():
        _write(path, _canonical_settings())
        return RepoConfig(path=path, created=True, added=list(DEFAULT_ALLOW))

    if force:
        prior = _prior_allow(path)
        _write(path, _canonical_settings())
        added = [entry for entry in DEFAULT_ALLOW if entry not in prior]
        return RepoConfig(path=path, created=False, added=added)

    settings = _load_settings(path)
    added = _merge_defaults(settings)
    if added:
        _write(path, settings)
    return RepoConfig(path=path, created=False, added=added)


def _canonical_settings() -> dict[str, object]:
    return {"permissions": {"defaultMode": DEFAULT_MODE, "allow": list(DEFAULT_ALLOW)}}


def _load_settings(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path} is not valid JSON; use --force to overwrite ({exc})"
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} is not a JSON object; use --force to overwrite")
    return data


def _merge_defaults(settings: dict[str, object]) -> list[str]:
    """Merge the default permissions into ``settings`` in place.

    Existing entries are preserved; ``defaultMode`` is only set when absent so a
    user's stricter or looser choice survives. Returns the allow entries added.
    """
    permissions = settings.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        raise ConfigError(
            "'permissions' is not a JSON object; use --force to overwrite"
        )
    permissions.setdefault("defaultMode", DEFAULT_MODE)

    allow = permissions.setdefault("allow", [])
    if not isinstance(allow, list):
        raise ConfigError(
            "'permissions.allow' is not a JSON list; use --force to overwrite"
        )

    added: list[str] = []
    for entry in DEFAULT_ALLOW:
        if entry not in allow:
            allow.append(entry)
            added.append(entry)
    return added


def _prior_allow(path: Path) -> list[str]:
    """Best-effort read of the existing allowlist; empty on any problem."""
    try:
        data = json.loads(path.read_text())
        allow = data["permissions"]["allow"]
    except (json.JSONDecodeError, OSError, KeyError, TypeError):
        return []
    return list(allow) if isinstance(allow, list) else []


def _write(path: Path, settings: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")
