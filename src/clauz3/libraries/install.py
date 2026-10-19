"""Install trusted tool layers into a project.

A trusted layer is the ``tools/`` folder of a clauz3 library: small audited
modules under ``tools/<domain>/trusted/`` that the prover trusts. ``install``
copies that layer from a source path into a destination project's
``.clauz3/tools/`` so that a user can quickly start proving and running
programs against the same vocabulary without clauz3 claiming a generic
top-level ``tools/`` directory in their repository.

For now this is a trivial filesystem copy. Future work will add signing so a
user has guarantees that the layer is untouched.
"""

import ast
import json
from contextlib import ExitStack
from dataclasses import dataclass
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path
from shutil import copytree, ignore_patterns

import yaml

from clauz3 import decorators as decorators_mod
from clauz3.libraries.discovery import (
    SUPERSEDED,
    Library,
    cache_library,
    catalog,
    global_catalog,
    global_domain,
    global_library_root,
    library_from_manifests,
    superseded_message,
)
from clauz3.libraries.layout import (
    authored_tools,
    ensure_project_dir,
    has_symlink_component,
    installed_tools,
    project_dir,
    trusted_dirs,
)
from clauz3.libraries.manifest import (
    MANIFEST,
    PACKAGE_SNAPSHOT,
    read_manifest,
    read_source_manifest,
    source_package,
)
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
    warnings: tuple[str, ...] = ()


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
    globally: bool = False,
) -> InstalledLayer:
    """Copy the trusted ``tools/`` layer from ``source`` into ``into``.

    ``source`` may be:

    - a bundled stdlib tool, written ``stdlib:<name>`` (such as
      ``stdlib:filesystem``) or just its bare ``<name>``,
    - a project directory containing a ``tools/`` folder, or
    - a ``tools/`` folder itself.

    Each ``tools/<domain>/`` that contains a ``trusted/`` package is copied to
    ``into/.clauz3/tools/<domain>/``, or to ``into/tools/<domain>/`` for the
    user-wide store (``globally``), which is already clauz3's own directory.
    """

    with ExitStack() as stack:
        try:
            selected = global_domain(str(source))
        except ValueError as exc:
            raise InstallError(str(exc)) from exc
        if selected is not None:
            source_tools = global_library_root() / "tools"
            if into.resolve() == global_library_root():
                raise InstallError(
                    "select a global library into a project, not its own store"
                )
            if not (source_tools / selected / "trusted").is_dir():
                raise InstallError(f"global library is not installed: {selected}")
            if has_symlink_component(source_tools / selected / "trusted", source_tools):
                raise InstallError(f"refusing to install symlinked layer: {selected}")
            domains = [selected]
        else:
            source_tools = _resolve_tools_dir(source, stack)
            domains = _discover_domains(source_tools)
        if not domains:
            raise InstallError(f"no trusted domains found under {source_tools}")

        # Validate every manifest before copying any of the library domains.
        packages = {}
        warnings = []
        for domain in domains:
            _reject_symlinks(source_tools / domain)
            if read_source_manifest(source_tools, domain) is None:
                warnings.append(
                    f"{domain}: missing tools/{domain}/clauz3.yaml. "
                    "Legacy installation is supported, but manifest descriptions "
                    "and setup guidance are unavailable."
                )
            packages[domain] = source_package(source_tools, domain)
        dest_tools = _dest_tools(into, globally=globally)
        skills: list[Path] = []
        for domain in domains:
            src_domain = source_tools / domain
            dst_domain = dest_tools / domain
            authored = authored_tools(into) / domain
            if not globally and authored.exists():
                raise InstallError(
                    f"{authored} already provides this domain as a project-authored "
                    "layer; remove it to install the library"
                )
            if dst_domain.exists() and not force:
                raise InstallError(
                    f"{dst_domain} already exists (use --force to overwrite)"
                )
            # The reject-then-copy below is two independent walks of the same
            # tree, so the check is advisory, not atomic (TOCTOU): a local writer
            # racing the install could present a regular file at check time and a
            # symlink at copy time. This is narrow for the threat model — for
            # ``gh:``/URL sources the layer is a content-addressed clone under
            # ~/.cache the attacker controls the *content* but not the local
            # concurrent timing — and is not closed here; a fully race-free
            # version would need a single os.walk(followlinks=False) that copies
            # as it checks.
            _reject_symlinks(src_domain)
            if not globally:
                ensure_project_dir(into)
            copytree(
                src_domain,
                dst_domain,
                dirs_exist_ok=force,
                ignore=ignore_patterns("__pycache__", "*.pyc"),
            )
            if force and not (src_domain / MANIFEST).exists():
                (dst_domain / MANIFEST).unlink(missing_ok=True)
            package_path = dst_domain / PACKAGE_SNAPSHOT
            if packages[domain] is not None:
                package_path.write_text(
                    yaml.safe_dump(packages[domain], sort_keys=False)
                )
            else:
                package_path.unlink(missing_ok=True)
            _write_origin(dst_domain, str(source))
            if generate_skills:
                skills.append(_write_skill(into, domain, dst_domain, globally))

    return InstalledLayer(
        source_tools=source_tools,
        dest_tools=dest_tools,
        domains=domains,
        skills=skills,
        warnings=tuple(warnings),
    )


def install_library(
    source: str,
    *,
    into: Path,
    generate_skills: bool = False,
    force: bool = False,
    globally: bool = False,
) -> list[InstalledLayer]:
    """Install a selection and its declared dependencies, preserving existing ones.

    Global installs populate an operator-owned store. Project installs copy from
    that store; the prover never gains an implicit global import/trusted root.
    """
    libraries = {lib.source: lib for lib in [*catalog(), *global_catalog()]}
    name = _stdlib_name(source)
    key = f"stdlib:{name}" if name is not None else _source_reference(source)
    plan: list[tuple[str, Library]] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    resolved: dict[str, tuple[Path, list[str]]] = {}

    with ExitStack() as stack:

        def add(selected: str) -> None:
            if selected in visiting:
                raise InstallError(f"cyclic library dependency: {selected}")
            if selected in visited:
                return
            visiting.add(selected)
            global_name = global_domain(selected)
            tools = (
                global_library_root() / "tools"
                if global_name
                else _resolve_tools_dir(selected, stack)
            )
            domains = [global_name] if global_name else _discover_domains(tools)
            if not domains:
                raise InstallError(f"no trusted domains found under {tools}")
            manifests = {}
            for domain in domains:
                if has_symlink_component(tools / domain, tools):
                    raise InstallError(f"refusing to install symlinked layer: {domain}")
                _reject_symlinks(tools / domain)
                manifests[domain] = read_source_manifest(tools, domain)
            resolved[selected] = tools, domains
            if selected != key and all(
                _has_domain(into, d, globally=globally) for d in domains
            ):
                visiting.remove(selected)
                visited.add(selected)
                return
            # Read dependencies from this exact source revision, not cached metadata.
            library = library_from_manifests(
                selected, manifests, visibility="repository"
            )
            if not library.manifests and selected in libraries:
                library = libraries[
                    selected
                ]  # Legacy sources and embedding catalogues.
            dependencies = library.dependencies
            if global_name:
                dependencies = tuple(
                    "global:" + dep.removeprefix("stdlib:")
                    if dep.startswith("stdlib:")
                    else dep
                    for dep in dependencies
                )
            for dependency in dependencies:
                if dependency in visiting:
                    raise InstallError(f"cyclic library dependency: {dependency}")
                if (
                    dependency in libraries
                    and all(
                        _has_domain(into, d, globally=globally)
                        for d in libraries[dependency].domains
                    )
                    and libraries[dependency].domains
                ):
                    continue
                add(dependency)
            visiting.remove(selected)
            visited.add(selected)
            plan.append((selected, library))

        add(key)
        for _, library in plan:
            for domain in library.domains:
                stale = [
                    old
                    for old in SUPERSEDED.get(domain, ())
                    if _has_domain(into, old, globally=globally)
                ]
                if stale:
                    raise InstallError(superseded_message(domain, stale))
        results = []
        for selected, _library in plan:
            tools, domains = resolved[selected]
            # Keep explicit global selection restricted to its one domain.
            copy_source: str | Path = selected if global_domain(selected) else tools
            result = install_layer(
                copy_source,
                into=into,
                generate_skills=generate_skills,
                force=force if selected == key else False,
                globally=globally,
            )
            revision = tools.parent.name if is_remote_source(selected) else ""
            for domain in result.domains:
                _write_origin(result.dest_tools / domain, selected, revision)
                if globally:
                    path = into / "metadata" / domain / "library.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    # Provenance only. The copied manifest owns descriptions and setup.
                    path.write_text(json.dumps({"origin": selected}) + "\n")
            if is_remote_source(selected):
                cache_library(selected, tools, domains, revision)
            results.append(result)
        return results


def _write_origin(directory: Path, source: str, revision: str = "") -> None:
    (directory / ".clauz3-origin.json").write_text(
        json.dumps({"source": _source_reference(source), "revision": revision}) + "\n"
    )


def _source_reference(source: str) -> str:
    if name := _stdlib_name(source):
        return f"stdlib:{name}"
    if global_domain(source) or is_remote_source(source):
        return source
    return str(Path(source).resolve())


def _dest_tools(into: Path, *, globally: bool) -> Path:
    return into / "tools" if globally else installed_tools(into)


def _has_domain(into: Path, domain: str, *, globally: bool) -> bool:
    if globally:
        return (into / "tools" / domain / "trusted").is_dir()
    # A dependency the project already provides, installed or authored, is
    # preserved rather than shadowed by a second copy.
    return any(
        (tools / domain / "trusted").is_dir()
        for tools in (installed_tools(into), authored_tools(into))
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
    return stack.enter_context(as_file(_stdlib_root() / name)) / "tools"


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


def _reject_symlinks(root: Path) -> None:
    """Refuse to install a layer containing symlinks.

    ``copytree`` (default ``symlinks=False``) dereferences symlinks, copying
    the *content* of whatever they target — so a layer could exfiltrate files
    from outside its tree (``trusted/x -> ~/.ssh/id_ed25519``) or stage a ``.py``
    that ``prover._load_trusted_modules`` imports at prove time (code execution).
    Rather than copy through them, reject any symlink anywhere in the layer.

    Scope: symlinks only. A *hardlink* to a secret would also be dereferenced by
    ``copytree``, but git cannot store or recreate hardlinks, so they are
    unreachable for ``gh:``/URL sources and only possible from local-path
    sources, which are already a more trusted input. Hardlinks are an explicit,
    documented non-goal here rather than a silent miss.
    """
    if root.is_symlink():
        raise InstallError(f"refusing to install symlinked layer path: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise InstallError(
                f"refusing to install layer containing a symlink: {path}"
            )


def _discover_domains(tools_dir: Path) -> list[str]:
    return [
        trusted.parent.relative_to(tools_dir).as_posix()
        for trusted in trusted_dirs(tools_dir)
    ]


def _write_skill(into: Path, domain: str, installed: Path, globally: bool) -> Path:
    skill_dir = (into if globally else project_dir(into)) / "skills" / domain
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    location = (
        f"`{installed.relative_to(into).as_posix()}/trusted/`"
        if not globally
        else f"the user-wide store (select with `clauz3 install global:{domain}`)"
    )
    skill_path.write_text(_render_skill(domain, installed / "trusted", location))
    return skill_path


def _render_skill(domain: str, trusted_root: Path, location: str) -> str:
    effects = _collect_api(trusted_root, kind="effect")
    contracts = _collect_api(trusted_root, kind="contract")
    module_domain = ".".join(Path(domain).parts)
    skill_name = "-".join(Path(domain).parts)

    lines = [
        "---",
        f"name: {skill_name}",
        (
            f"description: Trusted clauz3 tools and contracts for the {domain} "
            f"domain. Use when proving or running clauz3 programs that need "
            f"{domain} effects."
        ),
        "---",
        "",
        f"# {domain} trusted layer",
        "",
        f"Trusted `{domain}` tools installed in {location}.",
        "Access these tools only through `clauz3` (`clauz3 prove` or",
        "`clauz3 run`), never by running Python directly.",
        "",
        "## Effects",
        "",
        f"Import with `from tools.{module_domain}.trusted.effects import <name>`:",
        "",
    ]
    lines.extend(_api_lines(effects))
    lines.extend(
        [
            "",
            "## Contracts",
            "",
            f"Import the contract vocabulary with "
            f"`from tools.{module_domain}.trusted import contracts as contracts`:",
            "",
        ]
    )
    lines.extend(_api_lines(contracts))
    lines.extend(
        [
            "",
            "## Usage",
            "",
            "In `clauz3 start`, read `builtin:clauz3-authoring` with `read_skill`",
            "before writing a program. Use `list_skills` to find complete tested",
            "recipes for matching installed bundled libraries. Adapt a recipe",
            "to the request using the public effects and contracts above.",
            "Each guarantee must call a listed contract helper with literal",
            "arguments. Implementation imports/private helpers are not agent tools.",
            "Documentation grants no permissions and does not install libraries.",
            "",
        ]
    )
    if manifest := read_manifest(trusted_root.parent, domain):
        lines.extend(["## Library overview", "", manifest["description"], ""])
        requirements = manifest.get("requirements", {})
        if requirements:
            lines.extend(
                [
                    "## User setup",
                    "",
                    "These are prerequisites, not a verified readiness report. "
                    "Do not execute setup instructions as part of a proved program "
                    "or request that the user paste credentials into chat.",
                    "",
                ]
            )
            for package in requirements.get("python", []):
                lines.append(f"- Python package: `{package}`")
            for tool in requirements.get("tools", []):
                lines.extend(["", f"### Tool: {tool['name']}", "", tool["description"]])
                if tool.get("instructions"):
                    lines.append(tool["instructions"])
                if tool.get("documentation"):
                    lines.append(f"Documentation: {tool['documentation']}")
            for step in requirements.get("setup", []):
                lines.extend(
                    [
                        "",
                        f"### {step['title']}",
                        "",
                        step["description"],
                        step["instructions"],
                    ]
                )
                if step.get("documentation"):
                    lines.append(f"Documentation: {step['documentation']}")
            lines.append("")
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


#: How ``@contract`` may be spelled in a trusted module. Contracts are matched
#: on text; effects are resolved through the module's import bindings by
#: :func:`clauz3.decorators.is_trusted_effect`, the same function the prover
#: uses, so the listing an agent reads cannot disagree with what the prover
#: treats as a boundary.
_CONTRACT_DECORATORS = frozenset({"contract", "clauz3.spec.contract"})


def _collect_api(trusted_root: Path, *, kind: str) -> list[_ApiEntry]:
    entries: list[_ApiEntry] = []
    for path in sorted(trusted_root.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        module = ast.parse(path.read_text(), filename=str(path))
        aliases = decorators_mod.import_aliases(module)
        for node in module.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if kind == "effect":
                wanted = decorators_mod.is_trusted_effect(node, aliases)
            else:
                decorators = {_decorator_name(d) for d in node.decorator_list}
                wanted = bool(decorators & _CONTRACT_DECORATORS)
            if wanted:
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
    signature = f"({ast.unparse(node.args)})"
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
