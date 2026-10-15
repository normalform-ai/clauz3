/* Human-operated, read-only workspace browser. File content is always inert. */
(() => {
  const $ = (id) => document.getElementById(id);
  const panel = $("files-panel");
  let path = "",
    request;
  const node = (tag, value, cls) => {
    const element = document.createElement(tag);
    element.textContent = value;
    if (cls) element.className = cls;
    return element;
  };
  const size = (bytes) =>
    bytes < 1024
      ? `${bytes} B`
      : bytes < 1048576
        ? `${(bytes / 1024).toFixed(1)} KB`
        : `${(bytes / 1048576).toFixed(1)} MB`;
  function breadcrumbs() {
    const container = $("files-breadcrumbs");
    container.replaceChildren();
    const parts = path ? path.split("/") : [];
    ["Workspace", ...parts].forEach((name, index) => {
      if (index) container.append(node("span", "/"));
      const button = node("button", name);
      button.title = name;
      if (index === parts.length)
        button.setAttribute("aria-current", "location");
      button.addEventListener("click", () =>
        open(parts.slice(0, index).join("/")),
      );
      container.append(button);
    });
  }
  async function open(nextPath) {
    path = nextPath;
    request?.abort();
    const current = new AbortController();
    request = current;
    breadcrumbs();
    $("files-list").replaceChildren();
    $("file-preview").hidden = true;
    $("file-content").textContent = "";
    $("files-status").textContent = "Loading…";
    panel.setAttribute("aria-busy", "true");
    try {
      const query = new URLSearchParams({
        path,
        hidden: $("files-hidden").checked,
      });
      const response = await fetch(`/api/workspace/files?${query}`, {
        signal: current.signal,
        cache: "no-store",
      });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(
          response.status === 401
            ? "Sign in using the private link in your terminal."
            : result.detail || "Unable to read this path.",
        );
      }
      const result = await response.json();
      if (current.signal.aborted) return;
      $("files-status").textContent = "";
      if (result.kind === "directory") {
        for (const entry of result.entries) {
          const row = node("li", "");
          const button = node("button", "", "file-entry");
          const icon = node(
            "span",
            entry.kind === "directory" ? "▸" : "·",
            "file-icon",
          );
          icon.setAttribute("aria-hidden", "true");
          button.append(
            icon,
            node("span", entry.name, "file-name"),
            node(
              "span",
              entry.kind === "directory"
                ? "Folder"
                : entry.kind === "file"
                  ? size(entry.size)
                  : "Unavailable",
              "file-meta",
            ),
          );
          button.disabled = entry.kind === "unavailable";
          button.addEventListener("click", () =>
            open([path, entry.name].filter(Boolean).join("/")),
          );
          row.append(button);
          $("files-list").append(row);
        }
        $("files-status").textContent = result.truncated
          ? "Showing the first 2,000 entries. Open a folder to explore further."
          : result.entries.length
            ? `${result.entries.length} items`
            : "This folder is empty. Hidden files may be omitted.";
      } else {
        $("file-preview").hidden = false;
        $("file-size").textContent = size(result.size);
        $("file-preview-note").textContent =
          result.content === null
            ? "Preview unavailable. Only UTF-8 text files can be displayed."
            : result.truncated
              ? "Showing the first 256 KB. Refresh to see recent changes."
              : "";
        $("file-content").hidden = result.content === null;
        $("file-content").textContent = result.content || "";
      }
    } catch (error) {
      if (!current.signal.aborted)
        $("files-status").textContent =
          error.message || "Could not load files. Try refreshing.";
    } finally {
      if (!current.signal.aborted) panel.setAttribute("aria-busy", "false");
    }
  }
  const overlay = matchMedia("(max-width: 1100px)");
  function layout() {
    const modal = overlay.matches && !panel.hidden;
    document.querySelector("main").inert = modal;
    document.querySelector(".sidebar").inert = modal;
    document.body.style.overflow = modal ? "hidden" : "";
    if (modal) {
      panel.setAttribute("role", "dialog");
      panel.setAttribute("aria-modal", "true");
    } else {
      panel.removeAttribute("role");
      panel.removeAttribute("aria-modal");
    }
  }
  overlay.addEventListener("change", layout);
  function close() {
    panel.hidden = true;
    layout();
    document.body.classList.remove("files-open");
    $("files-toggle").setAttribute("aria-expanded", "false");
    $("files-toggle").focus();
  }
  $("files-toggle").addEventListener("click", () => {
    if (!panel.hidden) return close();
    panel.hidden = false;
    layout();
    document.body.classList.add("files-open");
    $("files-toggle").setAttribute("aria-expanded", "true");
    open(path);
    $("files-close").focus();
  });
  $("files-close").addEventListener("click", close);
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
    if (event.key === "Tab" && overlay.matches) {
      const focusable = [
        ...panel.querySelectorAll(
          'button:not(:disabled), input, [tabindex="0"]',
        ),
      ].filter((element) => element.getClientRects().length);
      const first = focusable[0],
        last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
  $("files-refresh").addEventListener("click", () => open(path));
  $("files-hidden").addEventListener("change", () => open(path));
  $("file-reference").addEventListener("click", () => {
    const prompt = $("prompt");
    if (prompt.disabled) {
      $("files-status").textContent =
        "Start a conversation before adding a path.";
      return;
    }
    const reference = JSON.stringify(path);
    if (prompt.value.length + reference.length + 1 > prompt.maxLength) {
      $("files-status").textContent =
        "Your message is full. Shorten it before adding a path.";
      return;
    }
    prompt.value +=
      (prompt.value && !/\s$/.test(prompt.value) ? " " : "") + reference;
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
    if (matchMedia("(max-width: 1100px)").matches) close();
    prompt.focus();
  });
})();
