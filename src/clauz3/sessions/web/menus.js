/* Popover menus anchored under the control that opened them. */
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
    menu.querySelector("button:not([hidden]):not(:disabled), a, input")?.focus();
  }
  function toggle(menu, trigger) {
    if (menu.matches(":popover-open") && triggers.get(menu) === trigger) menu.hidePopover();
    else open(menu, trigger);
  }
  globalThis.clauz3Menu = { open, toggle };
})();
