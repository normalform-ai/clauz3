# MCP integration

ClauZ3 uses the [Model Context Protocol](https://modelcontextprotocol.io/)
for both agent access and trusted wrappers around upstream tools:

| Integration | Where it lives | How you use it |
| --- | --- | --- |
| **Restricted agent session.** Source submission, installed-tool inspection, and read-only library discovery. | Built into `clauz3 start`. | Run `clauz3 start claude`; the launcher wires up the private bridge automatically. |
| **clauz3 as an MCP service.** An MCP server that exposes the prove → approve → execute flow so an MCP host (Claude Code/Desktop, Cursor, ...) can drive clauz3 directly. | A separate `clauz3-mcp` package. | Add to your harness config — `uvx clauz3-mcp`. There is nothing to "install". |
| **Wrap an untrusted MCP server in a trusted layer.** A TLE writes a small shim whose `@clauz3.trusted` functions forward into an upstream MCP server, so an agent's program can still be proved before any side effect runs. | `clauz3.mcp.MCPClient` in this package. | Import `MCPClient` in your trusted `effects.py`. |

The MCP SDK is now included in the base installation so `start` needs no extra
setup. The `[mcp]` extra remains accepted for compatibility. The rest of this
page covers trusted wrappers around upstream servers.

## Why a trusted shim?

A great deal of useful tooling already exists as MCP servers (filesystem,
mail, browser, calendar, ...). They are usually *unconstrained* — an agent
that holds the connection can call any tool with any arguments. The trusted
layer pattern in clauz3 puts a *proved* contract surface in front of each
side effect; you can apply the same pattern to an existing MCP server by
writing a small shim.

The shim is an ordinary trusted-layer module: each function uses
`@clauz3.trusted` and any `@clauz3.requires(...)` preconditions, then
forwards the call into the upstream MCP server via
:class:`clauz3.mcp.MCPClient`.

## Example

```python
# tools/files/trusted/effects.py
import clauz3
import deal

from clauz3.mcp import MCPClient

_files = MCPClient(
    command="uvx",
    args=["mcp-server-filesystem", "/srv"],
)


@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("read", "network")
@clauz3.trusted
def read_file(path: str) -> str:
    return _files.call("read_file", path=path)


@clauz3.requires(lambda path, content: len(path) > 0)
@deal.has("write", "network")
@clauz3.trusted
def write_file(path: str, content: str) -> None:
    _files.call("write_file", path=path, content=content)
```

Pair it with the usual `contracts.py`:

```python
# tools/files/trusted/contracts.py
from clauz3.spec import ContractSpec, contract, effect
from tools.files.trusted.effects import read_file, write_file

Read = effect(read_file)
Write = effect(write_file)


@contract
def read_only() -> ContractSpec:
    return Write.empty()


@contract
def only_write_under(prefix: str) -> ContractSpec:
    return Write.all(lambda e: e.path.startswith(prefix))
```

The agent then writes a program in the usual clauz3 style:

```python
import clauz3
from tools.files.trusted import contracts as files
from tools.files.trusted.effects import read_file, write_file


@clauz3.guarantee(files.only_write_under("/srv/outbox/"))
def main() -> None:
    body = read_file("/srv/inbox/order.txt")
    write_file("/srv/outbox/receipt.txt", body)
```

Submit it via `clauz3 run`. clauz3 proves the contract, asks the user to
approve the *guarantees*, and only then does the shim actually dispatch to
the upstream MCP server.

## Notes

- When adding `@deal.has(...)` runtime checks to a shim, include `"network"`, so
  [deal's runtime guard](deal.md) and `clauz3 lint` know the shim talks to a
  server.
  `MCPClient` opens internal sockets for its asyncio event loop, and
  `deal`'s runtime checker raises `OfflineContractError` if the declaration
  omits that permission. These optional markers do not determine which
  calls the `Read` and `Write` relations cover.
- `MCPClient` starts the upstream server lazily on the first call and
  reuses a single session for the process. It cleans itself up at
  interpreter exit; use `client.close()` or a `with` block for explicit
  teardown.
- Return values: scalar returns (FastMCP `-> str`/`-> int`) are unwrapped
  from `structuredContent={"result": ...}`; dict-shaped tools return their
  full structured payload; tools that return only text content return the
  first text block.
- Tool errors raise :class:`clauz3.mcp.MCPClientError`.
