/* Popover menus anchored under the control that opened them, plus theme presets. */
(() => {
  const triggers = new Map();
  function place(menu, trigger) {
    const box = trigger.getBoundingClientRect();
    const margin = 8;
    const left = Math.min(box.left, innerWidth - menu.offsetWidth - margin);
    const below = box.bottom + 4;
    const top =
      below + menu.offsetHeight > innerHeight - margin
        ? box.top - menu.offsetHeight - 4
        : below;
    menu.style.left = `${Math.max(margin, left)}px`;
    menu.style.top = `${Math.max(margin, top)}px`;
  }
  function open(menu, trigger) {
    if (!triggers.has(menu))
      menu.addEventListener("toggle", (event) => {
        triggers.get(menu)?.setAttribute("aria-expanded", String(event.newState === "open"));
      });
    triggers.set(menu, trigger);
    if (!menu.matches(":popover-open")) menu.showPopover();
    place(menu, trigger);
    trigger.setAttribute("aria-expanded", "true");
    (
      menu.querySelector('button[aria-checked="true"]:not([hidden]):not(:disabled)') ||
      menu.querySelector("button:not([hidden]):not(:disabled), a, input")
    )?.focus();
  }
  function toggle(menu, trigger) {
    if (menu.matches(":popover-open") && triggers.get(menu) === trigger) menu.hidePopover();
    else open(menu, trigger);
  }
  globalThis.clauz3Menu = { open, toggle };

  const STORAGE_KEY = "clauz3-theme";
  const DEFAULT_THEME = "parchment";
  const THEMES = [
    {
      id: "parchment",
      name: "Parchment",
      description: "Warm paper & terracotta",
      colors: ["#f6f2ea", "#ebe5d8", "#b44c27"],
    },
    {
      id: "indigo",
      name: "Formal Indigo",
      description: "Crisp slate & Z3 indigo",
      colors: ["#f6f7fb", "#ecedf6", "#4f46e5"],
    },
    {
      id: "obsidian",
      name: "Obsidian",
      description: "Dark carbon & emerald",
      colors: ["#0f1217", "#171b22", "#10b981"],
    },
    {
      id: "swiss",
      name: "Swiss Mono",
      description: "Gallery white & vermilion",
      colors: ["#f7f7f5", "#ededeb", "#d13415"],
    },
    {
      id: "solarized",
      name: "Lab Notebook",
      description: "Solarized sand & petrol teal",
      colors: ["#fdf6e3", "#eee8d5", "#0d7480"],
    },
    {
      id: "sage",
      name: "Sage",
      description: "Classic forest green",
      colors: ["#f8faf8", "#eef3ed", "#25664e"],
    },
  ];
  const byId = new Map(THEMES.map((theme) => [theme.id, theme]));
  let activeTheme = DEFAULT_THEME;

  function resolve(id) {
    return byId.get(id) || byId.get(DEFAULT_THEME);
  }
  function stored() {
    try {
      return globalThis.localStorage?.getItem(STORAGE_KEY) || "";
    } catch {
      return "";
    }
  }
  function save(id) {
    try {
      globalThis.localStorage?.setItem(STORAGE_KEY, id);
    } catch {}
  }
  function syncMenu() {
    const doc = globalThis.document;
    if (!doc?.getElementById) return;
    const current = doc.getElementById("theme-current");
    const theme = resolve(activeTheme);
    if (current) current.textContent = theme.name;
    const container = doc.getElementById("theme-options");
    if (!container?.children) return;
    for (const button of container.children) {
      const selected = button.dataset?.theme === theme.id;
      button.setAttribute("aria-checked", String(selected));
      const check = button.children?.[2];
      if (check) check.textContent = selected ? "✓" : "";
    }
  }
  function apply(id, { persist = true } = {}) {
    const theme = resolve(id);
    activeTheme = theme.id;
    const root = globalThis.document?.documentElement;
    if (root) {
      if (root.dataset) root.dataset.theme = theme.id;
      else root.setAttribute?.("data-theme", theme.id);
    }
    if (persist) save(theme.id);
    syncMenu();
    return theme;
  }
  function current() {
    return resolve(activeTheme);
  }
  function init() {
    const doc = globalThis.document;
    if (!doc?.getElementById) return;
    apply(stored() || DEFAULT_THEME, { persist: false });
    const picker = doc.getElementById("theme-picker");
    const menu = doc.getElementById("theme-menu");
    const container = doc.getElementById("theme-options");
    if (!picker || !menu || !container) return;
    const items = THEMES.map((theme) => {
      const button = doc.createElement("button");
      button.type = "button";
      button.setAttribute("role", "menuitemradio");
      button.dataset.theme = theme.id;
      const preview = doc.createElement("span");
      preview.className = "theme-preview";
      preview.setAttribute("aria-hidden", "true");
      for (const color of theme.colors) {
        const swatch = doc.createElement("span");
        if (swatch.style) swatch.style.background = color;
        preview.append(swatch);
      }
      const label = doc.createElement("span");
      label.className = "menu-label";
      label.textContent = theme.name;
      const desc = doc.createElement("small");
      desc.textContent = theme.description;
      label.append(desc);
      const check = doc.createElement("span");
      check.className = "menu-check";
      check.setAttribute("aria-hidden", "true");
      button.append(preview, label, check);
      button.addEventListener("click", () => apply(theme.id));
      return button;
    });
    container.replaceChildren(...items);
    syncMenu();
    picker.addEventListener("click", () => toggle(menu, picker));
    menu.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      event.preventDefault();
      const index = THEMES.findIndex((t) => t.id === activeTheme);
      const step = event.key === "ArrowDown" ? 1 : -1;
      const next = THEMES[(index + step + THEMES.length) % THEMES.length];
      apply(next.id);
      container.children[(index + step + THEMES.length) % THEMES.length]?.focus?.();
    });
  }
  globalThis.clauz3Theme = { THEMES, DEFAULT_THEME, apply, current, init };
  init();
})();
