"""Install trusted tool layers into a project.

A trusted layer is the ``tools/`` folder of a clauz3 project: small audited
modules under ``tools/<domain>/trusted/`` that the prover trusts. ``install``
copies that layer from a source path into a destination project so that a user
can quickly start proving and running programs against the same vocabulary.

For now this is a trivial filesystem copy. Future work will add signing so a
user has guarantees that the layer is untouched.
"""

import ast
from contextlib import ExitStack
from dataclasses import dataclass
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path
from shutil import copytree, ignore_patterns

from clauz3.source import is_remote_source, resolve_remote_source

#: Scheme that selects a tool bundled in ``clauz3``'s stdlib, e.g.
#: ``stdlib:filesystem``. Resolved via ``importlib.resources`` so it works the
#: same whether clauz3 is run from a source checkout or a ``pip install``ed
#: wheel.
STDLIB_SCHEME = "stdlib:"


class InstallError(Exception):
    """The install source or destination is invalid."""


@dataclass(frozen=True)
class InstalledLayer:
    """Result of installing a trusted layer."""

    source_tools: Path
    dest_tools: Path
    domains: list[str]
    skills: list[Path]


@dataclass(frozen=True)
class _ApiEntry:
    kind: str
    name: str
    signature: str
    doc: str | None


def install_layer(
    source: str | Path,
    *,
    into: Path,
    generate_skills: bool = False,
    force: bool = False,
) -> InstalledLayer:
    """Copy the trusted ``tools/`` layer from ``source`` into ``into``.

    ``source`` may be:

    - a bundled stdlib tool, written ``stdlib:<name>`` (such as
      ``stdlib:filesystem``) or just its bare ``<name>``,
    - a project directory containing a ``tools/`` folder, or
    - a ``tools/`` folder itself.

    Each ``tools/<domain>/`` that contains a ``trusted/`` package is copied to
    ``into/tools/<domain>/``.
    """

    with ExitStack() as stack:
        source_tools = _resolve_tools_dir(source, stack)
        domains = _discover_domains(source_tools)
        if not domains:
            raise InstallError(f"no trusted domains found under {source_tools}")

        dest_tools = into / "tools"
        skills: list[Path] = []
        for domain in domains:
            src_domain = source_tools / domain
            dst_domain = dest_tools / domain
            if dst_domain.exists() and not force:
                raise InstallError(
                    f"{dst_domain} already exists (use --force to overwrite)"
                )
            copytree(
                src_domain,
                dst_domain,
                dirs_exist_ok=force,
                ignore=ignore_patterns("__pycache__", "*.pyc"),
            )
            if generate_skills:
                skills.append(_write_skill(into, domain, dst_domain / "trusted"))

    return InstalledLayer(
        source_tools=source_tools,
        dest_tools=dest_tools,
        domains=domains,
        skills=skills,
    )


def _resolve_tools_dir(source: str | Path, stack: ExitStack) -> Path:
    """Resolve ``source`` to a concrete ``tools/`` directory.

    stdlib tools are located through ``importlib.resources`` and materialized to
    a real filesystem path via ``stack`` (a no-op for on-disk installs, an
    extraction for zipped ones), so the rest of the copy logic can use ordinary
    ``Path`` operations. Remote git sources (``gh:org/repo``, full URLs) are
    cloned into a content-addressed cache and then resolved like a local path.
    """
    stdlib_name = _stdlib_name(source)
    if stdlib_name is not None:
        return _resolve_stdlib_tools(stdlib_name, stack)

    if is_remote_source(source):
        path = resolve_remote_source(str(source))
    else:
        path = Path(source)
    if not path.exists():
        raise InstallError(f"source path does not exist: {path}{_stdlib_hint()}")
    if path.name == "tools" and path.is_dir():
        return path
    tools = path / "tools"
    if tools.is_dir():
        return tools
    raise InstallError(f"no tools/ directory found in {path}")


def _stdlib_name(source: str | Path) -> str | None:
    """Return the stdlib tool name ``source`` selects, or ``None``.

    Accepts the explicit ``stdlib:<name>`` scheme, or a bare ``<name>`` that
    matches a bundled tool and is not an existing local path.
    """
    text = str(source)
    if text.startswith(STDLIB_SCHEME):
        return text[len(STDLIB_SCHEME) :]
    if (
        text == Path(text).name
        and not Path(text).exists()
        and text in available_stdlib_tools()
    ):
        return text
    return None


def _resolve_stdlib_tools(name: str, stack: ExitStack) -> Path:
    tools = _stdlib_root() / name / "tools"
    if not tools.is_dir():
        raise InstallError(f"unknown stdlib tool: {name}{_stdlib_hint()}")
    return stack.enter_context(as_file(tools))


def _stdlib_root() -> Traversable:
    return files("clauz3") / "stdlib"


def available_stdlib_tools() -> list[str]:
    """Names of the bundled stdlib tools that ``install`` can resolve."""
    root = _stdlib_root()
    if not root.is_dir():
        return []
    return sorted(entry.name for entry in root.iterdir() if (entry / "tools").is_dir())


def _stdlib_hint() -> str:
    tools = available_stdlib_tools()
    if not tools:
        return ""
    available = ", ".join(f"{STDLIB_SCHEME}{name}" for name in tools)
    return f" (available stdlib tools: {available})"


def _discover_domains(tools_dir: Path) -> list[str]:
    return [
        trusted.parent.name
        for trusted in sorted(tools_dir.glob("*/trusted"))
        if trusted.is_dir()
    ]


def _write_skill(into: Path, domain: str, trusted_root: Path) -> Path:
    skill_dir = into / "agents" / "skills" / domain
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(_render_skill(domain, trusted_root))
    return skill_path


def _render_skill(domain: str, trusted_root: Path) -> str:
    effects = _collect_api(trusted_root, kind="effect")
    contracts = _collect_api(trusted_root, kind="contract")

    lines = [
        "---",
        f"name: {domain}",
        (
            f"description: Trusted clauz3 tools and contracts for the {domain} "
            f"domain. Use when proving or running clauz3 programs that need "
            f"{domain} effects."
        ),
        "---",
        "",
        f"# {domain} trusted layer",
        "",
        f"Trusted `{domain}` tools installed under `tools/{domain}/trusted/`.",
        "Access these tools only through `clauz3` (`clauz3 prove` or",
        "`clauz3 run`), never by running Python directly.",
        "",
        "## Effects",
        "",
        f"Import with `from tools.{domain}.trusted.effects import <name>`:",
        "",
    ]
    lines.extend(_api_lines(effects))
    lines.extend(
        [
            "",
            "## Contracts",
            "",
            f"Import the contract vocabulary with "
            f"`from tools.{domain}.trusted import contracts as {domain}`:",
            "",
        ]
    )
    lines.extend(_api_lines(contracts))
    lines.extend(
        [
            "",
            "## Usage",
            "",
            "State guarantees on the function you want proved:",
            "",
            "```python",
            "import clauz3",
            f"from tools.{domain}.trusted import contracts as {domain}",
        ]
    )
    if effects:
        lines.append(f"from tools.{domain}.trusted.effects import {effects[0].name}")
    lines.extend(
        [
            "",
            "",
            "@clauz3.guarantee(...)",
            "def main() -> None:",
            "    ...",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _api_lines(entries: list[_ApiEntry]) -> list[str]:
    if not entries:
        return ["_None available._"]
    lines = []
    for entry in entries:
        summary = _first_line(entry.doc)
        suffix = f" — {summary}" if summary else ""
        lines.append(f"- `{entry.name}{entry.signature}`{suffix}")
    return lines


def _first_line(doc: str | None) -> str:
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()


def _collect_api(trusted_root: Path, *, kind: str) -> list[_ApiEntry]:
    effect_decorators = {"deal.has", "has"}
    contract_decorators = {"contract", "clauz3.spec.contract"}
    wanted = effect_decorators if kind == "effect" else contract_decorators

    entries: list[_ApiEntry] = []
    for path in sorted(trusted_root.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        module = ast.parse(path.read_text(), filename=str(path))
        for node in module.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            decorators = {_decorator_name(d) for d in node.decorator_list}
            if decorators & wanted:
                entries.append(
                    _ApiEntry(
                        kind=kind,
                        name=node.name,
                        signature=_signature(node),
                        doc=ast.get_docstring(node),
                    )
                )
    return entries


def _signature(node: ast.FunctionDef) -> str:
    parts = []
    for arg in node.args.args:
        if arg.annotation is not None:
            parts.append(f"{arg.arg}: {ast.unparse(arg.annotation)}")
        else:
            parts.append(arg.arg)
    signature = f"({', '.join(parts)})"
    if node.returns is not None:
        signature += f" -> {ast.unparse(node.returns)}"
    return signature


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _decorator_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""
