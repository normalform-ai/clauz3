// Host-owned Pi SDK session: no native tools, resource discovery, or mutable user settings.
import { readFileSync, writeFileSync } from "node:fs";
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";
import { connectBridge } from "./pi_bridge.mjs";

export const TOOL_NAMES = ["run", "status", "tools", "libraries", "list_skills", "read_skill"]
  .map(name => `mcp__clauz3__${name}`);

// Only host-authored diagnostics may cross the startup boundary. SDK exceptions
// can contain credentials or private configuration, including in their messages.
class RestrictedPiError extends Error {}

export function startupFailure(error) {
  return { type: "response", id: "state", success: false,
    error: error instanceof RestrictedPiError ? error.message
      : "Restricted Pi startup failed; check runtime, authentication and model." };
}

export function checkTools(session) {
  const actual = session.getActiveToolNames().slice().sort();
  if (JSON.stringify(actual) !== JSON.stringify(TOOL_NAMES.slice().sort())) {
    throw new RestrictedPiError("Restricted Pi tool allowlist changed; session stopped");
  }
}

export async function createRestrictedSession(sdk, config, modelRuntime, tools) {
  if (sdk.VERSION !== "0.82.1") throw new RestrictedPiError("Unsupported restricted Pi SDK version");
  if (JSON.stringify(tools.map(t => t.name).sort()) !== JSON.stringify(TOOL_NAMES.slice().sort())) {
    throw new RestrictedPiError("Unexpected Clauz3 tool catalogue");
  }
  // Implement the SDK's ResourceLoader interface with an empty resource set.
  // Never instantiate discovery loaders, package managers, or config-file loaders.
  const extensions = { extensions: [], errors: [], runtime: sdk.createExtensionRuntime() };
  const resources = {
    getExtensions: () => extensions,
    getSkills: () => ({ skills: [], diagnostics: [] }),
    getPrompts: () => ({ prompts: [], diagnostics: [] }),
    getThemes: () => ({ themes: [], diagnostics: [] }),
    getAgentsFiles: () => ({ agentsFiles: [] }),
    getSystemPrompt: () => config.instructions,
    getAppendSystemPrompt: () => [],
    extendResources() { throw new RestrictedPiError("Resource discovery is disabled"); },
    async reload() {},
  };
  let model;
  if (config.model) {
    const matches = modelRuntime.getModels().filter(m =>
      `${m.provider}/${m.id}` === config.model || m.id === config.model);
    if (matches.length !== 1) throw new RestrictedPiError("Choose an exact Pi --model provider/model");
    [model] = matches;
  }
  const sessionManager = config.resume
    ? sdk.SessionManager.open(config.resume, config.sessionDirectory, config.cwd)
    : sdk.SessionManager.create(config.cwd, config.sessionDirectory);
  const { session, modelFallbackMessage } = await sdk.createAgentSession({
    cwd: config.cwd, agentDir: config.privateDirectory,
    modelRuntime, model, sessionManager,
    settingsManager: sdk.SettingsManager.inMemory({
      defaultProvider: config.defaultProvider, defaultModel: config.defaultModel,
      compaction: { enabled: false }, retry: { enabled: false },
    }),
    resourceLoader: resources,
    tools: TOOL_NAMES, customTools: tools,
  });
  if (!session.model || (config.resume && modelFallbackMessage)) {
    session.dispose();
    throw new RestrictedPiError(config.resume && modelFallbackMessage
      ? "Could not restore Pi model; start a new conversation with an available --model provider/model."
      : "No authenticated Pi model is available");
  }
  try { checkTools(session); }
  catch (error) { session.dispose(); throw error; }
  return session;
}

async function main() {
  const config = JSON.parse(readFileSync(process.argv[2], "utf8"));
  const sdk = await import(pathToFileURL(config.sdk).href);
  // Read auth through Pi's supported runtime; no credentials are copied into
  // the bridge config. Custom models and remote model-catalog loading are off.
  const modelRuntime = await sdk.ModelRuntime.create({
    authPath: config.authPath, modelsPath: null, allowModelNetwork: false,
  });
  const bridge = await connectBridge(config.bridge);
  let session;
  const emit = value => process.stdout.write(JSON.stringify(value) + "\n");
  try {
    session = await createRestrictedSession(sdk, config, modelRuntime, bridge.tools);
    session.subscribe(emit);
    writeFileSync(config.ready, JSON.stringify({
      version: sdk.VERSION, tools: session.getActiveToolNames(),
    }), { mode: 0o600 });
    const lines = createInterface({ input: process.stdin });
    for await (const line of lines) {
      const request = JSON.parse(line);
      try {
        checkTools(session);
        if (request.type === "get_state") {
          emit({ type: "response", id: request.id, success: true,
            data: { sessionFile: session.sessionFile } });
        } else if (request.type === "prompt" && typeof request.message === "string"
                   && !request.message.trimStart().startsWith("/")) {
          // The host offers text only: no RPC bash, reload, model or tool changes.
          await session.prompt(request.message, { expandPromptTemplates: false });
        } else {
          throw new RestrictedPiError("Operation unavailable in restricted Pi");
        }
      } catch (error) {
        emit({ type: "response", id: request.id, success: false, error: String(error.message) });
      }
    }
  } finally {
    session?.dispose();
    bridge.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {
    process.stdout.write(JSON.stringify(startupFailure(error)) + "\n", () => process.exit(1));
  });
}
