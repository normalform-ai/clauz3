"""Selected documentation is inert, bounded, and fixed for a session."""

import asyncio
from pathlib import Path

import pytest

from clauz3.sessions import skills as docs
from clauz3.sessions.tools import create_session_tools


def skill(root: Path, name: str, text: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_previews_snapshot_and_id_only_reads(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    path = skill(
        selected,
        "example",
        "---\nname: example\ndescription: Useful docs\n"
        "allowed-tools: Bash\n---\nRun !`touch escaped`\n",
    )
    skill(tmp_path / "unselected", "private", "SECRET")
    snapshots = docs.snapshot_skills([selected, selected])
    assert len(snapshots) == 1
    doc = snapshots[0]
    assert "Useful docs" in doc.preview
    assert "touch escaped" not in doc.preview
    assert "allowed-tools: Bash" in doc.preview
    server = create_session_tools(tmp_path, [], snapshots)
    path.write_text("Changed after startup")
    skill(selected, "added", "Not selected at startup")

    async def inspect() -> None:
        names = {tool.name for tool in await server.list_tools()}
        assert names == {
            "tools",
            "libraries",
            "run",
            "status",
            "list_skills",
            "read_skill",
        }
        listing = await server.call_tool("list_skills", {})
        assert "Useful docs" in str(listing)
        assert "SECRET" not in str(listing)
        assert "Not selected at startup" not in str(listing)
        assert "touch escaped" in str(
            await server.call_tool("read_skill", {"id": doc.id})
        )
        for invalid in (str(path), "../../etc/passwd", "unknown"):
            with pytest.raises(Exception, match="Unknown skill ID"):
                await server.call_tool("read_skill", {"id": invalid})

    asyncio.run(inspect())
    assert not (tmp_path / "escaped").exists()
    assert docs.snapshot_skills([selected]) != snapshots


@pytest.mark.parametrize(
    "text",
    [
        "# Plain document\nHelpful instructions",
        "---\nmalformed frontmatter",
        "---\n!!python/object:danger\n---\nBody",
    ],
)
def test_frontmatter_is_never_evaluated(tmp_path: Path, text: str) -> None:
    skill(tmp_path, "one", text)
    doc = docs.snapshot_skills([tmp_path])[0]
    assert doc.content == text
    assert doc.preview


def test_duplicate_names_have_distinct_stable_ids(tmp_path: Path) -> None:
    for parent in ("a", "b"):
        skill(tmp_path / parent, "same", "Documentation")
    first = docs.snapshot_skills([tmp_path])
    assert len({doc.id for doc in first}) == 2
    assert first == docs.snapshot_skills([tmp_path])


@pytest.mark.parametrize("directory", [False, True])
def test_symlinks_refused(tmp_path: Path, directory: bool) -> None:
    outside = skill(tmp_path / "outside", "secret", "private")
    selected = tmp_path / "selected"
    selected.mkdir()
    if directory:
        (selected / "linked").symlink_to(outside.parent, target_is_directory=True)
    else:
        (selected / "SKILL.md").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink|escapes"):
        docs.snapshot_skills([selected])


@pytest.mark.parametrize(
    "limit", ["MAX_DOCUMENT_BYTES", "MAX_TOTAL_BYTES", "MAX_DOCUMENTS", "MAX_ENTRIES"]
)
def test_selection_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    skill(tmp_path, "one", "text")
    monkeypatch.setattr(docs, limit, 0)
    with pytest.raises(ValueError, match="exceeds"):
        docs.snapshot_skills([tmp_path])
