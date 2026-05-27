"""Macros for inlining repo files into the mkdocs site.

Four primitives, all callable from any .md page via ``{{ ... }}``:

- ``include_file(path)`` — inline one file in a fenced block.
- ``include_markdown(path)`` — inline a markdown file as rendered markdown
  (no fence). Use this to surface a tool's ``README.md`` on a doc page.
- ``list_dir(path, glob="**/*")`` — bulleted listing of matching files.
- ``include_dir(path, glob="**/*", sort="path", heading_level=4)`` — iterate
  and inline each matching file.

Paths are resolved relative to the repo root (the directory containing
``mkdocs.yml``).
"""

from __future__ import annotations

from pathlib import Path

_EXTENSION_LANGUAGE = {
    ".py": "python",
    ".pyi": "python",
    ".md": "markdown",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".sh": "bash",
    ".bash": "bash",
    ".html": "html",
    ".css": "css",
    ".txt": "",
}

_FILENAME_LANGUAGE = {
    "Justfile": "just",
    "Makefile": "make",
    "Dockerfile": "dockerfile",
}

_SKIP_DIR_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _language_for(path: Path) -> str:
    if path.name in _FILENAME_LANGUAGE:
        return _FILENAME_LANGUAGE[path.name]
    return _EXTENSION_LANGUAGE.get(path.suffix, "")


def _should_skip(path: Path) -> bool:
    return any(part in _SKIP_DIR_PARTS for part in path.parts)


def _resolve(root: Path, raw: str) -> Path:
    candidate = (root / raw).resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"macros: path does not exist: {raw!r}")
    return candidate


def _matching_files(directory: Path, glob: str) -> list[Path]:
    return sorted(
        p
        for p in directory.glob(glob)
        if p.is_file() and not _should_skip(p.relative_to(directory))
    )


def define_env(env):  # type: ignore[no-untyped-def]
    root = Path(env.project_dir).resolve()

    @env.macro  # type: ignore[misc]
    def include_file(path: str, language: str | None = None) -> str:
        target = _resolve(root, path)
        lang = language if language is not None else _language_for(target)
        body = target.read_text()
        rel = target.relative_to(root)
        fence = f"```{lang}" if lang else "```"
        return f"`{rel}`\n\n{fence}\n{body.rstrip()}\n```"

    @env.macro  # type: ignore[misc]
    def include_markdown(path: str) -> str:
        """Inline a markdown file as rendered markdown (no fence).

        Use this to surface a tool's existing ``README.md`` on a doc page
        instead of duplicating the prose. Unlike ``include_file``, the body
        is returned as-is so markdown structure (headings, code blocks,
        tables) renders normally.
        """
        target = _resolve(root, path)
        return target.read_text().rstrip()

    @env.macro  # type: ignore[misc]
    def list_dir(path: str, glob: str = "**/*") -> str:
        directory = _resolve(root, path)
        if not directory.is_dir():
            raise NotADirectoryError(f"macros: not a directory: {path!r}")
        files = _matching_files(directory, glob)
        if not files:
            return "_(no matching files)_"
        lines = []
        for f in files:
            rel_to_root = f.relative_to(root)
            rel_to_dir = f.relative_to(directory)
            lines.append(f"- `{rel_to_dir}` — `{rel_to_root}`")
        return "\n".join(lines)

    @env.macro  # type: ignore[misc]
    def include_dir(
        path: str,
        glob: str = "**/*",
        sort: str = "path",
        heading_level: int = 4,
    ) -> str:
        directory = _resolve(root, path)
        if not directory.is_dir():
            raise NotADirectoryError(f"macros: not a directory: {path!r}")
        files = _matching_files(directory, glob)
        if sort == "name":
            files = sorted(files, key=lambda p: p.name)
        elif sort != "path":
            raise ValueError(f"macros: unknown sort {sort!r}")
        if not files:
            return "_(no matching files)_"
        heading = "#" * max(1, min(6, heading_level))
        chunks = []
        for f in files:
            rel = f.relative_to(root)
            lang = _language_for(f)
            fence = f"```{lang}" if lang else "```"
            body = f.read_text().rstrip()
            chunks.append(f"{heading} `{rel}`\n\n{fence}\n{body}\n```")
        return "\n\n".join(chunks)
