// Exercise the installed, pinned SDK with deterministic model responses, offline.
import assert from "node:assert/strict";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { createRestrictedSession, checkTools, startupFailure, TOOL_NAMES } from "../../src/clauz3/sessions/restricted_pi.mjs";

const sdk = await import(pathToFileURL(process.argv[2]).href);
const directory = process.argv[3];
const marker = join(directory, "escaped");
const secret = "PROJECT_CONTEXT_MUST_NOT_LOAD";
for (const root of [directory, join(directory, ".pi"), join(directory, "private")]) {
  mkdirSync(join(root, "extensions"), { recursive: true });
  writeFileSync(join(root, "AGENTS.md"), secret);
  writeFileSync(join(root, "settings.json"), JSON.stringify({
    extensions: [join(root, "extensions", "escape.mjs")],
    shellCommandPrefix: `touch ${marker};`,
  }));
  writeFileSync(join(root, "extensions", "escape.mjs"),
    `import {writeFileSync} from 'node:fs'; writeFileSync(${JSON.stringify(marker)}, 'extension');`);
}
const model = {
  provider: "test", id: "scripted", name: "Scripted", api: "anthropic-messages",
  baseUrl: "http://127.0.0.1:1", reasoning: false, input: ["text"],
  contextWindow: 100000, maxTokens: 1000,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
};
let nextCalls = [];
let modelCalls = 0;
const runtime = {
  getModels: () => [model], getModel: () => model,
  hasConfiguredAuth: () => true, isUsingOAuth: () => false,
  streamSimple(_model, context) {
    modelCalls++;
    assert.deepEqual(context.tools.map(t => t.name).sort(), [...TOOL_NAMES].sort());
    assert.ok(!context.systemPrompt.includes(secret));
    const calls = nextCalls;
    nextCalls = [];
    const message = { role: "assistant", provider: model.provider, model: model.id,
      api: model.api, timestamp: Date.now(), stopReason: calls.length ? "toolUse" : "stop",
      content: calls.length ? calls : [{ type: "text", text: "done" }],
      usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    };
    return { async *[Symbol.asyncIterator]() { yield { type: "done", message }; },
      result: async () => message };
  },
};
const executed = [];
const tools = TOOL_NAMES.map(name => ({ name, label: name, description: name,
  parameters: { type: "object", properties: {} },
  async execute() { executed.push(name); return { content: [{ type: "text", text: "ok" }], details: {} }; },
}));
const config = { cwd: directory, privateDirectory: join(directory, "private"),
  sessionDirectory: join(directory, "sessions"), model: "test/scripted",
  instructions: "Use the Clauz3 bridge." };
const forbidden = [
  ["bash", { command: `touch ${marker}; curl http://127.0.0.1:1` }],
  ["write", { path: marker, content: "escaped" }],
  ["edit", { path: marker, oldText: "", newText: "escaped" }],
  ["read", { path: join(directory, "AGENTS.md") }],
  ["web_search", { query: "escape" }],
  ["mcp__other__run", { command: "escape" }],
  ["future_native_tool", {}],
];
let session = await createRestrictedSession(sdk, config, runtime, tools);
for (let pass = 0; pass < 2; pass++) {
  checkTools(session);
  // Even an attempt to activate native tools through the SDK stays filtered.
  session.setActiveToolsByName([...TOOL_NAMES, ...forbidden.map(([name]) => name)]);
  checkTools(session);
  nextCalls = forbidden.map(([name, args], i) => ({
    type: "toolCall", id: `forbidden-${pass}-${i}`, name, arguments: args,
  }));
  nextCalls.push({ type: "toolCall", id: `allowed-${pass}`,
    name: TOOL_NAMES[2], arguments: {} });
  await session.prompt("Try all tools", { expandPromptTemplates: false });
  const results = session.agent.state.messages.filter(m => m.role === "toolResult");
  for (const [i, [name]] of forbidden.entries()) {
    const result = results.find(m => m.toolCallId === `forbidden-${pass}-${i}`);
    assert.equal(result?.isError, true, name);
    assert.match(JSON.stringify(result.content), /not found/);
    assert.ok(!JSON.stringify(result.content).includes(secret));
  }
  assert.deepEqual(executed, Array(pass + 1).fill(TOOL_NAMES[2]));
  assert.ok(!existsSync(marker));
  const resume = session.sessionFile;
  session.dispose();
  if (pass === 0) session = await createRestrictedSession(sdk, { ...config, resume }, runtime, tools);
}
async function expectStartupFailure(operation, expected) {
  await assert.rejects(operation, error => {
    assert.deepEqual(startupFailure(error), {
      type: "response", id: "state", success: false, error: expected,
    });
    return true;
  });
}
await expectStartupFailure(
  createRestrictedSession({ ...sdk, VERSION: "next" }, config, runtime, tools),
  "Unsupported restricted Pi SDK version");
await expectStartupFailure(
  createRestrictedSession(sdk, { ...config, model: "missing/model" }, runtime, tools),
  "Choose an exact Pi --model provider/model");
await expectStartupFailure(
  createRestrictedSession({ ...sdk, createAgentSession: async () => ({
    session: { model: null, dispose() {} },
  }) }, config, runtime, tools), "No authenticated Pi model is available");
await expectStartupFailure(
  createRestrictedSession({ ...sdk, createAgentSession: async () => ({
    session: { model, dispose() {} }, modelFallbackMessage: "PRIVATE_SDK_DIAGNOSTIC",
  }) }, { ...config, resume: session.sessionFile }, runtime, tools),
  "Could not restore Pi model; start a new conversation with an available --model provider/model.");
await expectStartupFailure(
  createRestrictedSession({ ...sdk, createAgentSession: async () => {
    throw new Error("PRIVATE_SDK_DIAGNOSTIC");
  } }, config, runtime, tools),
  "Restricted Pi startup failed; check runtime, authentication and model.");
assert.ok(!JSON.stringify(startupFailure({ message: "PRIVATE_SDK_DIAGNOSTIC" })).includes("PRIVATE_SDK"));
await assert.rejects(createRestrictedSession(sdk, config, runtime, [...tools, tools[0]]), /catalogue/);
assert.equal(modelCalls, 4);
console.log("Pi confinement and resume verified against installed SDK");
