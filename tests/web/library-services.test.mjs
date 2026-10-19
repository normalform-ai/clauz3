import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.value = "";
    this.hidden = true;
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll() { return []; }
}

test("bundle switches select each service independently and preserve other libraries", async () => {
  const nodes = new Map();
  const document = new Element("document");
  document.getElementById = id => {
    if (!nodes.has(id)) nodes.set(id, new Element("div"));
    return nodes.get(id);
  };
  document.createElement = tag => new Element(tag);
  const gmail = "google_workspace/gmail";
  const sheets = "google_workspace/sheets";
  const requests = [];
  const payload = {
    catalog: [{
      source: "stdlib:google_workspace", title: "Google Workspace",
      domains: [gmail, sheets], dependencies: [], requirements: [],
      manifests: [{ name: gmail, title: "Gmail" }, { name: sheets, title: "Sheets" }],
      description: "Google services", visibility: "bundled", installed: true,
    }],
    installed: [gmail, sheets, "filesystem"], active: [],
    selected: ["filesystem", gmail],
  };
  const context = vm.createContext({
    document,
    fetch: async (url, options) => {
      if (options.method === "POST") {
        assert.equal(url, "/api/workspace/libraries/select");
        const { domains } = JSON.parse(options.body);
        requests.push(domains);
        return { ok: true, json: async () => ({ selected: domains }) };
      }
      return { ok: true, json: async () => payload };
    },
  });
  vm.runInContext(readFileSync(new URL("../../src/clauz3/sessions/web/libraries.js", import.meta.url), "utf8"), context);
  await document.listeners["clauz3:libraries"]();
  const descendants = node => [node, ...node.children.flatMap(descendants)];
  const switches = () => descendants(nodes.get("library-list")).filter(node => node.tagName === "input");
  const find = title => switches().find(node => node.attributes["aria-label"] === `Use ${title} in new conversations`);
  assert.equal(find("Gmail").checked, true);
  assert.equal(find("Sheets").checked, false);
  assert.equal(find("Google Workspace"), undefined); // No all-service switch.
  find("Sheets").checked = true;
  await find("Sheets").listeners.change();
  assert.deepEqual(requests[0], ["filesystem", gmail, sheets]);
  find("Gmail").checked = false;
  await find("Gmail").listeners.change();
  assert.deepEqual(requests[1], ["filesystem", sheets]);
});

test("composer and picker distinguish mock, mixed and unmarked libraries", async () => {
  const nodes = new Map();
  const document = new Element("document");
  document.getElementById = id => {
    if (!nodes.has(id)) nodes.set(id, new Element("div"));
    return nodes.get(id);
  };
  document.createElement = tag => new Element(tag);
  const context = vm.createContext({ document,
    fetch: async () => ({ ok: true, json: async () => ({
      catalog: [], installed: ["email", "mixed", "real"], active: [], selected: [],
      mockDeclarations: {
        email: { kind: "mock", declared_mock: ["send_email"] },
        mixed: { kind: "mixed", declared_mock: ["preview"], unmarked: ["send"] },
        real: { kind: "unmarked", declared_mock: [] },
      },
    }) }),
  });
  vm.runInContext(readFileSync(new URL("../../src/clauz3/sessions/web/libraries.js", import.meta.url), "utf8"), context);
  await new Promise(resolve => setImmediate(resolve));
  document.listeners["clauz3:state"]({ detail: { conversationId: "one", libraries: ["email", "mixed", "real"] } });
  const chips = nodes.get("libraries").children;
  assert.equal(chips[0].children[0].textContent, "🧪 Mock");
  assert.equal(chips[1].children[0].textContent, "🧪 Mixed");
  assert.equal(chips[2].children.length, 0);
  nodes.get("library-menu-toggle").listeners.click();
  const rows = nodes.get("library-menu-list").children;
  assert.equal(rows[0].children[0].children[0].children[0].textContent, "🧪 Mock");
  assert.equal(rows[1].children[0].children[0].children[0].textContent, "🧪 Mixed");
});
