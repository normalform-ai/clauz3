// Session-local Pi extension. Uses the existing MCP runtime, no Node SDK dependency.
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { createInterface } from "node:readline";

export async function connectBridge(config) {
  const child = spawn(config.command, config.args, {
    env: { ...process.env, ...config.env }, stdio: ["pipe", "pipe", "ignore"],
  });
  const pending = new Map();
  let sequence = 0;
  let disconnected = false;
  function fail() {
    disconnected = true;
    for (const request of pending.values()) request.reject(new Error("Clauz3 MCP bridge disconnected"));
    pending.clear();
  }
  child.on("error", fail);
  child.on("exit", fail);
  child.stdin.on("error", fail);
  const lines = createInterface({ input: child.stdout });
  lines.on("line", (line) => {
    try {
      const response = JSON.parse(line);
      const request = pending.get(response.id);
      if (request) {
        pending.delete(response.id);
        if (response.error) request.reject(new Error(response.error.message));
        else request.resolve(response.result);
      } else if (response.id !== undefined && response.method) {
        child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: response.id,
          error: { code: -32601, message: "Client capability disabled" } }) + "\n");
      }
    } catch { fail(); child.kill(); }
  });
  function call(method, params) {
    return new Promise((resolve, reject) => {
      if (disconnected) return reject(new Error("Clauz3 MCP bridge disconnected"));
      const id = ++sequence;
      pending.set(id, { resolve, reject });
      child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  }
  const close = () => { process.removeListener("exit", close); child.kill(); fail(); };
  process.once("exit", close);
  try {
  await call("initialize", { protocolVersion: "2024-11-05", capabilities: {},
    clientInfo: { name: "clauz3-pi", version: "0.1.2" } });
  child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
  const { tools } = await call("tools/list", {});
  const names = ["run", "status", "tools", "libraries", "list_skills", "read_skill"];
  if (JSON.stringify(tools.map(tool => tool.name).sort()) !== JSON.stringify(names.sort())) {
    throw new Error("Clauz3 bridge has an unexpected tool catalogue");
  }
  const definitions = tools.map((tool) => ({
      name: `mcp__clauz3__${tool.name}`, label: `Clauz3 ${tool.name}`,
      description: tool.description || tool.name, parameters: tool.inputSchema,
      async execute(_id, args) {
        const result = await call("tools/call", { name: tool.name, arguments: args });
        if (result.isError) throw new Error(result.content?.filter(c => c.type === "text").map(c => c.text).join("\n") || "Clauz3 tool failed");
        return { content: result.content, details: {} };
      },
    }));
  return { tools: definitions, close };
  } catch (error) {
    close();
    throw error;
  }
}

export default async function (pi) {
  const config = JSON.parse(readFileSync(process.env.CLAUZ3_PI_BRIDGE, "utf8"));
  const bridge = await connectBridge(config);
  pi.on("session_shutdown", bridge.close);
  for (const tool of bridge.tools) pi.registerTool(tool);
  writeFileSync(config.ready, "ready", { mode: 0o600 });
}
