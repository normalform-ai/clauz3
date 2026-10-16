# Restricted Codex launcher: configuration gap

Follow-up to [#264](https://github.com/cmungall/agent-deal/issues/264).
Research snapshot: 2026-10-14, installed `codex-cli 0.162.1`.

The intended interface remains `clauz3 start codex`, with the same owned MCP
bridge, library selection, fresh approval service and credential separation
as the Claude adapter. An experimental adapter is available in UI and CLI with
explicit `--unrestricted`; restricted Codex remains gated.

The available interfaces inspected were `codex --help`, `codex features list`,
`codex debug models --bundled`, and the stable and experimental JSON schemas
emitted by `codex app-server generate-json-schema`. The official
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
and [security documentation](https://learn.chatgpt.com/docs/security) describe
separate controls for shell execution, local image access, web search, apps,
MCP servers, skills and managed configuration.

`features.shell_tool = false` disables the default shell tool. It is not a
blanket tool allowlist. The bundled model catalog independently specifies an
`apply_patch_tool_type`; the documented `tools` settings do not establish a
single switch to remove every native tool. Likewise, per-skill enablement
and MCP per-server tool allowlists do not establish isolation from all other
configuration sources. A read-only execution sandbox is not equivalent to
allowing only clauz3 tool calls: it permits reads outside the selected libraries.

The inspection has **not established** a supported configuration that enforces
the required MCP-only surface. This is a research gap, not a claim that no
future Codex version or integration can provide it. Do not implement this by
listing today's native tool names in a denylist, changing model metadata to
hide tools, or relying on the prompt to prevent their use.

The official [TypeScript SDK](https://github.com/openai/codex/tree/main/sdk/typescript)
wraps the CLI and exposes sandbox, approval, and configuration options, but no
exact native/MCP tool allowlist. Codex's Rust extension API contains an internal
`ToolPolicy` allowlist, captured at thread creation; the inspected public CLI,
SDK, and app-server interface do not expose it. Surfacing it would require a
custom runtime build and ongoing maintenance. This project will not maintain
that fork. Revisit when a supported interface exposes the required boundary.

Before enabling restricted mode:

1. Identify a supported tool allowlist and configuration-isolation boundary,
   or an app-server execution gateway that can enforce it independently of
   tool discovery. Establish how native skills, hooks, plugins, apps, managed
   settings and other MCP sources interact with that boundary.
2. Define supported CLI versions and reject unsupported versions before
   starting the approval service. Preserve authentication without copying
   private credentials into the agent-visible bridge configuration.
3. Implement and inspect the actual model-visible and executable tool surface,
   then exercise discovery, proof, signed approval, execution and cleanup.
4. Add the harness adapter to the existing lifecycle and managed resume flow;
   do not silently substitute a permissive/debug session when restrictions
   cannot be enforced.

For now, `start codex` fails with guidance to opt into `--unrestricted` or use
Claude/Pi. Their restricted integrations and selected read-only skill documents
do not depend on the outcome of this investigation.
