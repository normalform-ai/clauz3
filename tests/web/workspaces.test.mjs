import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

class Element {
  constructor() { this.listeners = {}; this.children = []; this.hidden = false; this.dataset = {}; this.value = ""; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  dispatchEvent(event) { this.listeners[event.type]?.(event); }
  querySelectorAll() { return []; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  matches() { return false; }
  setAttribute(name, value) { (this.attributes ??= {})[name] = value; }
  focus() {}
}

function fixture() {
  const nodes = new Map();
  const document = new Element();
  document.getElementById = id => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  document.createElement = () => new Element();
  const sent = [];
  document.addEventListener("clauz3:send", event => sent.push(event.detail));
  const context = vm.createContext({ document, Event, CustomEvent, clauz3Menu: { open() {} },
    fetch: async () => ({ ok: true, json: async () => ({ enabled: true, tutorial: true, current: "/project", recent: [{ name: "Project", path: "/project", available: true }, { name: "Gone", path: "/gone", available: false }] }) }),
  });
  vm.runInContext(readFileSync(new URL("../../src/clauz3/sessions/web/workspaces.js", import.meta.url), "utf8"), context);
  return { nodes, document, sent };
}

test("reset requires an explicit confirmation and sends only the tutorial identifier", async () => {
  const { nodes, sent } = fixture();
  await new Promise(resolve => setImmediate(resolve));
  nodes.get("tutorial-reset").listeners.click();
  assert.equal(sent.length, 0);
  assert.match(nodes.get("workspace-confirm-text").textContent, /files, conversations, and remembered approvals/);
  assert.equal(nodes.get("workspace-confirm-yes").textContent, "Reset tutorial");
  nodes.get("workspace-confirm-yes").listeners.click();
  assert.equal(sent[0].method, "clauz3/workspace");
  assert.deepEqual(JSON.parse(JSON.stringify(sent[0].params)), { tutorial: "email", reset: true, confirmReset: true, stop: true });
});

test("server busy response prompts for stop and preserves the intended destination", async () => {
  const { nodes, document, sent } = fixture();
  await new Promise(resolve => setImmediate(resolve));
  document.dispatchEvent(new CustomEvent("clauz3:confirm-workspace", { detail: { path: "/another/project" } }));
  assert.equal(sent.length, 0);
  assert.equal(nodes.get("workspace-confirm-yes").textContent, "Stop and switch");
  nodes.get("workspace-confirm-yes").listeners.click();
  assert.deepEqual(JSON.parse(JSON.stringify(sent[0].params)), { path: "/another/project", stop: true });
});

test("starter prompts are editable and do not submit actions", () => {
  const { nodes, sent } = fixture();
  nodes.get("tutorial-prompts").listeners.click({ target: { closest: () => ({ dataset: { prompt: "send" } }) } });
  assert.match(nodes.get("prompt").value, /installed mock email library/);
  assert.match(nodes.get("prompt").value, /bob@example.com/);
  assert.equal(sent.length, 0);
});

test("recent workspaces mark the current one and explain missing folders", async () => {
  const { nodes } = fixture();
  await new Promise(resolve => setImmediate(resolve));
  const [current, missing] = nodes.get("workspace-recents").children.map(li => li.children[0]);
  assert.equal(current.attributes["aria-current"], "true");
  assert.equal(current.children[1].children[0].textContent, "/project");
  assert.equal(missing.disabled, true);
  assert.equal(missing.children[1].children[0].textContent, "Folder not found");
});

test("the tutorial workspace is labelled and offers reset from the menu", async () => {
  const { nodes, sent } = fixture();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(nodes.get("workspace-picker").dataset.tutorial, "true");
  assert.equal(nodes.get("project-name").textContent, "Email approvals");
  assert.equal(nodes.get("workspace-reset").hidden, false);
  nodes.get("workspace-reset").listeners.click();
  assert.equal(sent.length, 0);
  assert.equal(nodes.get("workspace-confirm-yes").textContent, "Reset tutorial");
});
