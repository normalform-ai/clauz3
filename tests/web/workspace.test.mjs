// Run with `just node-test` (part of `just test` and `just ui-test`).
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(
  new URL("../../src/clauz3/sessions/web/workspace.js", import.meta.url),
  "utf8",
);

// Just enough DOM for workspace.js: a tree, class selectors and listeners.
class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this.attributes = {};
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.own = "";
    this.parent = null;
    this.classList = { toggle() {} };
    if (tag === "template") this.content = new Element();
  }
  get textContent() {
    return this.own + this.children.map((child) => child.textContent).join("");
  }
  set textContent(value) {
    this.own = String(value);
    this.children = [];
  }
  append(...nodes) {
    for (const node of nodes) {
      node.parent = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }
  remove() {
    if (this.parent)
      this.parent.children = this.parent.children.filter((child) => child !== this);
  }
  cloneNode() {
    return new Element(this.tag);
  }
  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }
  dispatchEvent() {}
  querySelectorAll(selector) {
    const name = selector.startsWith(".") ? selector.slice(1) : null;
    const found = [];
    const visit = (node) => {
      for (const child of node.children) {
        if (name && child.className.split(" ").includes(name)) found.push(child);
        visit(child);
      }
    };
    visit(this);
    return found;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] ?? null;
  }
  closest() {
    return new Element();
  }
  contains() {
    return false;
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
  hasAttribute(name) {
    return name in this.attributes;
  }
  focus() {}
}

/** Load workspace.js into a fresh page and open its socket. */
function page() {
  const nodes = new Map();
  const $ = (id) => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  const document = new Element();
  document.getElementById = $;
  document.createElement = (tag) => new Element(tag);
  document.body = new Element();
  document.activeElement = null;
  const sidebar = new Element();
  document.querySelector = () => sidebar;
  const sockets = [];
  const errors = [];
  const context = vm.createContext({
    document,
    console: { error: (...args) => errors.push(args) },
    CustomEvent,
    Event,
    JSON,
    WebSocket: class {
      constructor(url) {
        this.url = url;
        this.sent = [];
        sockets.push(this);
      }
      send(data) {
        this.sent.push(JSON.parse(data));
      }
    },
    location: { protocol: "http:", host: "127.0.0.1", pathname: "/ui", assign() {} },
    history: { pushState() {} },
    sessionStorage: { getItem: () => "", setItem() {} },
    fetch: async () => ({
      ok: true,
      json: async () => ({ conversations: [], history: [] }),
      text: async () => "",
    }),
    setInterval() {},
    setTimeout() {},
    clearTimeout() {},
    clauz3SignedOut: { active: false, close: () => false },
    clauz3Copy: { button: () => new Element("button"), bind() {} },
    clauz3History: { group: () => [] },
    clauz3Menu: { toggle() {} },
    clauz3ToolCards: { render() {} },
    clauz3Markdown: {
      render(markdown) {
        if (markdown.includes("UNRENDERABLE")) throw new Error("render failed");
        const node = new Element();
        node.textContent = markdown;
        return node;
      },
    },
  });
  context.window = context;
  context.innerHeight = 0;
  context.scrollY = 0;
  context.scrollTo = () => {};
  context.addEventListener = () => {};
  vm.runInContext(source, context);
  const [socket] = sockets;
  socket.onopen();
  return {
    $,
    socket,
    errors,
    receive: (method, params = {}) =>
      socket.onmessage({ data: JSON.stringify({ method, params }) }),
  };
}

const providers = ["codex", "opencode"].map((id) => ({
  id,
  label: id,
  available: true,
  enabled: true,
}));
const state = (values = {}) => ({
  agent: "codex",
  agentLabel: "Codex",
  providers,
  conversationId: "chat",
  sessionId: "session",
  cwd: "/project",
  turn: 0,
  runs: [],
  libraries: [],
  ...values,
});
const user = (text, turn) => ({ method: "clauz3/user", params: { text, turn } });
const update = (value) => ({ method: "session/update", params: { update: value } });
const reply = (text) =>
  update({ sessionUpdate: "agent_message_chunk", content: { type: "text", text } });

test("a saved message that cannot be shown does not truncate the transcript", () => {
  const { $, receive, errors } = page();
  receive("clauz3/restore", {
    ...state({ turn: 2 }),
    history: [
      user("one", 1),
      reply("UNRENDERABLE"),
      { method: "clauz3/turn_end", params: {} },
      user("two", 2),
      reply("second answer"),
    ],
  });
  assert.equal(errors.length, 1);
  assert.equal(errors[0][1].message, "render failed");
  const [first, second] = $("conversation").querySelectorAll(".turn");
  assert.match(first.textContent, /^one/);
  assert.equal(second.textContent, "twosecond answer");
  // The restored state was still applied.
  assert.equal($("project-path").textContent, "/project");
  // Replay has ended: a live message again clears the draft it echoes.
  $("prompt").value = "three";
  receive("clauz3/user", { text: "three", turn: 3 });
  assert.equal($("prompt").value, "");
});

test("plan entries without a text status are still listed", () => {
  const { $, receive } = page();
  receive("clauz3/restore", { ...state({ turn: 1 }), history: [user("plan", 1)] });
  receive("session/update", {
    update: {
      sessionUpdate: "plan",
      entries: [
        { content: "Read the files", status: "in_progress" },
        { content: "Write a summary" },
        { content: "Send it", status: null },
      ],
    },
  });
  const [plan] = $("conversation").querySelectorAll(".plan");
  assert.deepEqual(
    plan.children.map((item) => item.textContent),
    ["Read the files · in progress", "Write a summary", "Send it"],
  );
  receive("session/update", { update: { sessionUpdate: "plan", entries: null } });
  assert.deepEqual(plan.children, []);
});
