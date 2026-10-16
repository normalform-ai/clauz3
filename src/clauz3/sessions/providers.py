"""Optional coding-agent runtimes. No provider SDK is imported by Clauz3."""

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

PI_VERSION = "0.82.1"


def pi_sdk(executable: str) -> Path:
    """Inspect the installed package without executing its startup configuration."""
    entry = Path(executable).resolve()
    root = entry.parent.parent
    try:
        package = json.loads((root / "package.json").read_text())
        valid = (
            package.get("name") == "@earendil-works/pi-coding-agent"
            and package.get("version") == PI_VERSION
            and entry == root / "dist/cli.js"
            and (root / "dist/index.js").is_file()
            and shutil.which("node") is not None
        )
    except (OSError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError(
            "Restricted Pi requires the npm runtime "
            f"@earendil-works/pi-coding-agent@{PI_VERSION} "
            "and Node.js. Install that version or explicitly use --unrestricted."
        )
    return root / "dist/index.js"


def check_runtime(name: str, command: list[str], unrestricted: bool) -> None:
    if name == "pi" and not unrestricted:
        pi_sdk(command[-1])


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    executable: str
    args: tuple[str, ...]
    install: str
    restricted: bool = False


PROVIDERS = {
    p.name: p
    for p in (
        Provider(
            "claude",
            "Claude",
            "claude-agent-acp",
            (),
            "npm install -g @agentclientprotocol/claude-agent-acp@0.89.1",
            True,
        ),
        Provider(
            "codex",
            "Codex",
            "codex-acp",
            (),
            "npm install -g @agentclientprotocol/codex-acp@2.2.2",
        ),
        Provider(
            "agy",
            "Antigravity",
            "agy",
            (),
            "install Antigravity CLI from https://antigravity.google/download",
        ),
        Provider(
            "opencode", "OpenCode", "opencode", ("acp",), "npm install -g opencode-ai"
        ),
        Provider(
            "pi",
            "Pi",
            "pi",
            (),
            f"npm install -g @earendil-works/pi-coding-agent@{PI_VERSION}",
            True,
        ),
    )
}


def provider(name: str) -> Provider:
    try:
        return PROVIDERS[name]
    except (KeyError, TypeError):
        raise ValueError(
            f"unsupported agent: {name}; choose {', '.join(PROVIDERS)}"
        ) from None


def check_mode(name: str, unrestricted: bool, *, browser_ui: bool = False) -> None:
    if name == "claude" and unrestricted and browser_ui:
        raise ValueError(
            "Claude browser sessions do not support --unrestricted. "
            "Restart without that flag, or use clauz3 start claude --unrestricted "
            "for native terminal tools."
        )
    if not provider(name).restricted and not unrestricted:
        raise ValueError(
            f"{provider(name).label} confinement is not yet verified. "
            "Use --unrestricted to opt into native tools that can bypass "
            "Clauz3 proofs and approval, or choose claude or pi for restricted mode."
        )


def command(name: str) -> list[str]:
    selected = provider(name)
    executable = shutil.which(selected.executable)
    if executable is None:
        raise ValueError(f"{selected.executable} is not on PATH; {selected.install}")
    if name in {"agy", "pi"}:
        return [
            sys.executable,
            "-P",
            "-m",
            "clauz3.sessions.native_adapter",
            name,
            executable,
        ]
    return [executable, *selected.args]


def inventory(unrestricted: bool) -> list[dict[str, object]]:
    return [
        {
            "id": p.name,
            "label": p.label,
            "available": shutil.which(p.executable) is not None,
            "enabled": (unrestricted or p.restricted)
            and not (p.name == "claude" and unrestricted),
            "modeHint": "restart without --unrestricted"
            if p.name == "claude" and unrestricted
            else "requires --unrestricted"
            if not p.restricted and not unrestricted
            else "",
            "install": p.install,
        }
        for p in PROVIDERS.values()
    ]
