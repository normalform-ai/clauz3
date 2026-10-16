// Deterministic model -> real restricted Pi SDK -> real Clauz3 MCP/proof/approval.
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { connectBridge } from "../../src/clauz3/sessions/pi_bridge.mjs";
import { createRestrictedSession, TOOL_NAMES } from "../../src/clauz3/sessions/restricted_pi.mjs";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const sdk = await import(pathToFileURL(config.sdk).href);
const directory = mkdtempSync(join(tmpdir(), "clauz3-pi-proof-"));
const bridge = await connectBridge(config.bridge);
const model = { provider: "test", id: "scripted", name: "Scripted",
  api: "anthropic-messages", reasoning: false, input: ["text"],
  contextWindow: 100000, maxTokens: 1000,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
};
let called = false;
const runtime = { getModels: () => [model], hasConfiguredAuth: () => true,
  streamSimple(_model, context) {
    assert.deepEqual(context.tools.map(t => t.name).sort(), [...TOOL_NAMES].sort());
    const content = called ? [{ type: "text", text: "done" }] : [{ type: "toolCall",
      id: "run", name: "mcp__clauz3__run", arguments: { source: config.source } }];
    const message = { role: "assistant", provider: model.provider, model: model.id,
      api: model.api, timestamp: Date.now(), stopReason: called ? "stop" : "toolUse", content,
      usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    };
    called = true;
    return { async *[Symbol.asyncIterator]() { yield { type: "done", message }; },
      result: async () => message };
  },
};
let session;
try {
  session = await createRestrictedSession(sdk, {
    cwd: directory, privateDirectory: directory, sessionDirectory: directory,
    model: "test/scripted", instructions: "Call the Clauz3 run tool.",
  }, runtime, bridge.tools);
  await session.prompt("Run the fixture", { expandPromptTemplates: false });
  const result = session.agent.state.messages.find(m => m.role === "toolResult");
  assert.ok(result);
  console.log(JSON.stringify(result));
} finally {
  session?.dispose();
  bridge.close();
  rmSync(directory, { recursive: true, force: true });
}
