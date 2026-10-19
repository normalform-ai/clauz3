/* Clipboard actions are explicit user gestures. Text is copied as text, never HTML. */
(() => {
  async function write(value) {
    const pending = Promise.resolve(value).then((text) => {
      if (typeof text !== "string") throw new Error("Nothing to copy");
      return text;
    });
    // Observe a rejected fetch even if the browser refuses clipboard access first.
    pending.catch(() => {});
    if (navigator.clipboard?.write && typeof ClipboardItem !== "undefined") {
      // Start the clipboard operation during the click, even when producing the
      // text requires a request. Safari otherwise loses the user activation.
      const blob = pending.then(
        (text) => new Blob([text], { type: "text/plain" }),
      );
      blob.catch(() => {});
      await navigator.clipboard.write([
        new ClipboardItem({ "text/plain": blob }),
      ]);
    } else if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(await pending);
    } else {
      const text = await pending;
      const previous = document.activeElement;
      const field = document.createElement("textarea");
      field.value = text;
      field.className = "clipboard-fallback";
      field.setAttribute("readonly", "");
      document.body.append(field);
      try {
        field.select();
        if (!document.execCommand("copy"))
          throw new Error(
            "Clipboard access is unavailable; select and copy the text manually.",
          );
      } finally {
        field.remove();
        previous?.focus();
      }
    }
  }
  function bind(button, read) {
    const caption = button.textContent;
    const label = button.getAttribute("aria-label") || caption;
    const title = button.title || label;
    let reset;
    button.type = "button";
    button.setAttribute("aria-live", "polite");
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (button.dataset.copying) return;
      clearTimeout(reset);
      button.dataset.copying = "true";
      button.disabled = true;
      button.textContent = "Copying…";
      button.setAttribute("aria-label", `${label}: copying`);
      try {
        await write(read());
        button.textContent = "Copied";
        button.setAttribute("aria-label", `${label}: copied`);
        button.title = "Copied to clipboard";
      } catch (error) {
        button.textContent = "Copy failed";
        button.setAttribute("aria-label", `${label}: copy failed`);
        button.title = error?.message || "Could not copy to clipboard";
      } finally {
        delete button.dataset.copying;
        button.disabled = false;
        reset = setTimeout(() => {
          button.textContent = caption;
          button.setAttribute("aria-label", label);
          button.title = title;
        }, 2500);
      }
    });
    return button;
  }
  function button(label, read, caption = "Copy") {
    const element = document.createElement("button");
    element.className = "copy-button";
    element.textContent = caption;
    element.setAttribute("aria-label", label);
    element.title = label;
    return bind(element, read);
  }
  function block(pre, label = "Copy code") {
    const container = document.createElement("div");
    container.className = "copy-block";
    const toolbar = document.createElement("div");
    toolbar.className = "copy-toolbar";
    toolbar.append(button(label, () => pre.textContent));
    container.append(toolbar, pre);
    return container;
  }
  globalThis.clauz3Copy = { write, bind, button, block };
})();
