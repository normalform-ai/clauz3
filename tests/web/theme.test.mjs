import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

class Element {
  constructor() {
    this.listeners = {};
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.textContent = "";
  }
  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.children = children;
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
  matches() {
    return false;
  }
  showPopover() {}
  hidePopover() {}
  getBoundingClientRect() {
    return { left: 10, bottom: 30, top: 10 };
  }
  querySelector() {
    return this.children[0] || null;
  }
  focus() {
    this.focused = true;
  }
}

function fixture(initialStored = "") {
  const nodes = new Map();
  const store = new Map(initialStored ? [["clauz3-theme", initialStored]] : []);
  const document = new Element();
  document.documentElement = new Element();
  document.getElementById = (id) => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  document.createElement = () => new Element();
  const localStorage = {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => store.set(key, String(value)),
  };
  const context = vm.createContext({
    document,
    localStorage,
    innerWidth: 1024,
    innerHeight: 768,
  });
  context.globalThis = context;
  vm.runInContext(
    readFileSync(
      new URL("../../src/clauz3/sessions/web/menus.js", import.meta.url),
      "utf8",
    ),
    context,
  );
  return { nodes, document, store, theme: context.clauz3Theme };
}

test("initializes with default parchment theme and renders all preset options", () => {
  const { nodes, document, theme } = fixture();
  assert.equal(document.documentElement.dataset.theme, "parchment");
  assert.equal(nodes.get("theme-current").textContent, "Parchment");
  const options = nodes.get("theme-options").children;
  assert.equal(options.length, theme.THEMES.length);
  assert.equal(options[0].attributes["aria-checked"], "true");
  assert.equal(options[0].children[2].textContent, "✓");
  assert.equal(options[1].attributes["aria-checked"], "false");
});

test("restores saved theme from localStorage and falls back safely on unknown values", () => {
  const saved = fixture("obsidian");
  assert.equal(saved.document.documentElement.dataset.theme, "obsidian");
  assert.equal(saved.nodes.get("theme-current").textContent, "Obsidian");

  const unknown = fixture("nonexistent-theme");
  assert.equal(unknown.document.documentElement.dataset.theme, "parchment");
});

test("clicking an option or pressing arrow keys switches and persists the theme", () => {
  const { nodes, document, store } = fixture();
  const options = nodes.get("theme-options").children;
  const indigoButton = options.find((b) => b.dataset.theme === "indigo");
  indigoButton.listeners.click();
  assert.equal(document.documentElement.dataset.theme, "indigo");
  assert.equal(store.get("clauz3-theme"), "indigo");
  assert.equal(nodes.get("theme-current").textContent, "Formal Indigo");
  assert.equal(indigoButton.attributes["aria-checked"], "true");

  nodes.get("theme-menu").listeners.keydown({
    key: "ArrowDown",
    preventDefault() {},
  });
  assert.equal(document.documentElement.dataset.theme, "obsidian");
  assert.equal(store.get("clauz3-theme"), "obsidian");
});
