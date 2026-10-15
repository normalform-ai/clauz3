"""Built-in recipes prove and execute with the matching bundled libraries."""

import ast
import asyncio
import re
from pathlib import Path

import pytest

from clauz3.approvals.client import MockApprovalServer
from clauz3.approvals.receipts import SIGNING_KEY_ENV, VERIFY_KEY_ENV, generate_keypair
from clauz3.libraries.install import install_layer
from clauz3.libraries.layout import expand_import_roots
from clauz3.runner import RunProofError, discover_trusted_roots, run_source
from clauz3.sessions.authoring import AUTHORING_ID, RECIPES, builtin_documents
from clauz3.sessions.catalogue import snapshot_catalogue
from clauz3.sessions.tools import create_session_tools


@pytest.mark.parametrize("outside", [False, True])
@pytest.mark.parametrize("name", RECIPES)
def test_complete_recipe_proves_and_executes(
    name: str,
    outside: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for domain in RECIPES[name][1]:
        install_layer(f"stdlib:{domain}", into=tmp_path)
    roots = discover_trusted_roots(import_roots=expand_import_roots([tmp_path]))
    document = next(
        d
        for d in builtin_documents(tmp_path, roots)
        if d.id == f"builtin:recipe:{name}"
    )
    programs = re.findall(r"```python\n(.*?)```", document.content, re.S)
    assert len(programs) == 1
    source = programs[0]
    assert "..." not in source
    if outside:
        tree = ast.parse(source)
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
        paths = [
            n
            for stmt in main.body
            for n in ast.walk(stmt)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and n.value.startswith("./data/")
        ]
        assert paths
        original_path = paths[0].value
        assert isinstance(original_path, str)
        paths[0].value = original_path.replace("./data/", "./outside/", 1)
        source = ast.unparse(tree)
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "input.txt").write_text("hello world\nother line\n")
    (data / "other.txt").write_text("another hello\nHELLO\n")
    if name == "grep-directory":
        (data / "report..txt").write_text("hello from a skipped file\n")
    signing, verifying = generate_keypair()
    monkeypatch.setenv(SIGNING_KEY_ENV, signing)
    monkeypatch.setenv(VERIFY_KEY_ENV, verifying)
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "cache"))
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        if outside:
            # Keep the shipped guarantees unchanged while moving one actual
            # effect path outside their prefix. Refusal must precede dispatch.
            def forbidden_execution(*args: object, **kwargs: object) -> None:
                pytest.fail("a mutated recipe reached execution")

            monkeypatch.setattr("clauz3.runner._execute_source", forbidden_execution)
            with pytest.raises(RunProofError):
                run_source(
                    source,
                    source_name=f"{name}.py",
                    trusted_roots=roots,
                    import_roots=expand_import_roots([tmp_path]),
                    approval_service_url=server.url,
                )
            assert server.requests == []
            assert capsys.readouterr().out == ""
            return
        outcome = run_source(
            source,
            source_name=f"{name}.py",
            trusted_roots=roots,
            import_roots=expand_import_roots([tmp_path]),
            approval_service_url=server.url,
        )
    assert outcome.approval.approved
    assert len(server.requests) == 1
    assert server.requests[0]["guarantees"]
    output = capsys.readouterr().out
    if name == "read-file":
        assert output == "hello world\nother line\n\n"
    elif name == "count-lines":
        assert output == "2\n"
    elif name == "write-and-read":
        assert (data / "result.txt").read_text() == "hello"
        assert output == "hello\n"
    elif name in {"grep-named-files", "grep-directory"}:
        assert "hello world" in output and "another hello" in output
        assert "other line" not in output and "HELLO" not in output
        if name == "grep-directory":
            assert "Search incomplete; skipped unsupported path:" in output
            assert "report..txt" in output
            assert "hello from a skipped file" not in output
    elif name == "list-files":
        paths = ast.literal_eval(output.strip())
        assert len(paths) == 2
        assert {Path(path) for path in paths} == {
            Path("data/input.txt"),
            Path("data/other.txt"),
        }
    assert (data / "input.txt").read_text() == "hello world\nother line\n"


def test_builtin_guide_requires_no_native_skills_or_libraries(tmp_path: Path) -> None:
    async def inspect() -> None:
        server = create_session_tools(tmp_path, [])
        names = {t.name for t in await server.list_tools()}
        assert {"list_skills", "read_skill"} <= names
        docs = await server.call_tool("list_skills", {})
        assert AUTHORING_ID in str(docs)
        assert "builtin:recipe:" not in str(docs)
        text = str(await server.call_tool("read_skill", {"id": AUTHORING_ID}))
        assert "range(50)" in text and "truncates" in text
        assert "awaiting_approval" in text
        assert "clauz3._" in text

    asyncio.run(inspect())


def test_recipe_selection_requires_unchanged_selected_libraries(tmp_path: Path) -> None:
    install_layer("stdlib:filesystem", into=tmp_path)
    install_layer("stdlib:grep", into=tmp_path)
    roots = discover_trusted_roots(import_roots=expand_import_roots([tmp_path]))
    docs = builtin_documents(tmp_path, roots)
    assert {d.name for d in docs} == {
        "clauz3-authoring",
        "read-file",
        "count-lines",
        "write-and-read",
        "grep-named-files",
    }
    # A similarly named custom library is not a tested version of the recipe.
    effects = tmp_path / ".clauz3/tools/filesystem/trusted/effects.py"
    effects.write_text(effects.read_text() + "\n# local modification\n")
    assert [d.id for d in builtin_documents(tmp_path, roots)] == [AUTHORING_ID]
    assert [d.id for d in builtin_documents(tmp_path, [])] == [AUTHORING_ID]


def test_api_is_inert_and_separate_from_implementation(tmp_path: Path) -> None:
    root = tmp_path / "tools/example/trusted"
    root.mkdir(parents=True)
    path = root / "effects.py"
    path.write_text(
        "import os\nimport clauz3 as cz\n"
        "from clauz3.spec import contract as rule\n"
        "raise RuntimeError('discovery executed code')\n"
        "CONSTANT = '/tmp/data'\n"
        "def private_implementation(): pass\n"
        "@cz.trusted\n@cz.requires(lambda value: value > 0)\n"
        "def act(value: int = 1, *, dry_run: bool = False) -> str:\n"
        "    return os.getcwd()\n"
        "@rule\ndef limit(n: int): pass\n"
        "class Item(cz.Row):\n    name: str\n"
    )
    catalogue = snapshot_catalogue(tmp_path, [root])
    api = "\n".join(catalogue["api"])
    assert "act(value: int=1, *, dry_run: bool=False) -> str" in api
    assert "cz.requires(lambda value: value > 0)" in api
    assert "Contract (use inside @clauz3.guarantee): limit(n: int)" in api
    assert "Row type: from tools.example.trusted.effects import Item" in api
    assert "import os" not in api
    assert "private_implementation" not in api
    assert "CONSTANT" not in api
    assert "import os" in "\n".join(catalogue["source"])

    async def inspect() -> None:
        server = create_session_tools(tmp_path, [root])
        path.write_text("# changed after snapshot\n")
        assert "act(value" in str(await server.call_tool("tools", {"query": "act"}))
        source = await server.call_tool(
            "tools", {"query": "os.getcwd", "view": "source"}
        )
        assert "not agent tools" in str(source)
        assert "os.getcwd" in str(source)
        assert "No installed tools match" in str(
            await server.call_tool("tools", {"query": "os.getcwd"})
        )

    asyncio.run(inspect())


@pytest.mark.parametrize("decorator", ["cz.contract", "rule"])
def test_catalogue_supports_root_contract_exports_and_package_modules(
    tmp_path: Path, decorator: str
) -> None:
    root = tmp_path / "tools/example/trusted"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text(
        "import clauz3 as cz\nfrom clauz3 import contract as rule\n"
        f"@{decorator}\ndef limit(n: int): pass\n"
        "@cz.trusted\ndef not_an_effect(): pass\n"
    )
    api = "\n".join(snapshot_catalogue(tmp_path, [root])["api"])
    assert "Contract (use inside @clauz3.guarantee): limit(n: int)" in api
    assert "Import: from tools.example.trusted import limit" in api
    assert "from tools.example.trusted.__init__" not in api
    assert "not_an_effect" not in api


def test_catalogue_unfolds_chained_preconditions(tmp_path: Path) -> None:
    root = tmp_path / "tools/example/trusted"
    root.mkdir(parents=True)
    (root / "effects.py").write_text(
        "import deal as d\n"
        "@d.chain(d.has('io'), d.chain(d.pre(lambda x: x > 0)))\n"
        "def act(x: int) -> None: pass\n"
    )
    api = "\n".join(snapshot_catalogue(tmp_path, [root])["api"])
    assert "Effect: act(x: int) -> None" in api
    assert "Preconditions:\nd.pre(lambda x: x > 0)" in api


@pytest.mark.parametrize("invalid", [b"def broken(:\n", b"\x00", b"\xff"])
def test_invalid_library_keeps_read_only_discovery_available(
    tmp_path: Path, invalid: bytes
) -> None:
    root = tmp_path / "tools/example/trusted"
    root.mkdir(parents=True)
    (root / "effects.py").write_bytes(invalid)
    (root / "contracts.py").write_text(
        "from clauz3 import contract\n@contract\ndef limit(n: int): pass\n"
    )

    async def inspect() -> None:
        server = create_session_tools(tmp_path, [root])
        api = str(await server.call_tool("tools", {}))
        assert "tools/example/trusted/effects.py" in api
        assert "Cannot parse API:" in api
        if invalid.startswith(b"def"):
            assert "line 1" in api
        assert "Contract (use inside @clauz3.guarantee): limit" in api
        assert AUTHORING_ID in str(await server.call_tool("list_skills", {}))
        assert "filesystem" in str(await server.call_tool("libraries", {}))
        if invalid.startswith(b"def"):
            assert "def broken(:" in str(
                await server.call_tool("tools", {"view": "source"})
            )
        result = str(await server.call_tool("run", {"source": "def main(): pass"}))
        assert "phase: refused" in result
        assert "executed:" not in result

    asyncio.run(inspect())


@pytest.mark.parametrize("requires", ["clauz3", "clauz3.conditions"])
@pytest.mark.parametrize("row", ["clauz3", "clauz3.row"])
def test_catalogue_uses_supported_precondition_and_row_imports(
    tmp_path: Path, requires: str, row: str
) -> None:
    root = tmp_path / "tools/example/trusted"
    root.mkdir(parents=True)
    (root / "effects.py").write_text(
        "import clauz3\n"
        f"from {requires} import requires as pre\n"
        f"from {row} import Row as Record\n"
        "@clauz3.trusted\n@pre(lambda x: x > 0)\n"
        "def act(x: int) -> None: pass\n"
        "class Item(Record):\n    name: str\n"
    )
    api = "\n".join(snapshot_catalogue(tmp_path, [root])["api"])
    assert "Preconditions:\npre(lambda x: x > 0)" in api
    assert "Row type: from tools.example.trusted.effects import Item" in api
