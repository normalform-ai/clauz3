/* Operator-only library controls; no install capability is exposed to agents.
   The Libraries page installs libraries and sets the default for new
   conversations; the message-box menu changes the current conversation. */
(() => {
  const $ = (id) => document.getElementById(id);
  let catalog = [],
    installed = [],
    selected = [],
    active = [],
    mockDeclarations = {},
    state = {},
    busy = false,
    filter = "all",
    pending = null,
    confirming = false;
  let reviewId = null;
  const modal = $("library-review");
  const text = (tag, value, cls) => {
    const n = document.createElement(tag);
    n.textContent = value;
    if (cls) n.className = cls;
    return n;
  };
  const send = (method, params) =>
    document.dispatchEvent(
      new CustomEvent("clauz3:send", { detail: { method, params } }),
    );
  const sameSet = (a, b) =>
    JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
  const running = () =>
    state.busy ||
    state.stopping ||
    (state.runs || []).some(
      (r) => !["completed", "failed", "refused", "cancelled"].includes(r.phase),
    );
  const guidance = {
    "Python package duckdb":
      "Make duckdb available in the workspace’s Python environment. With uv run, add --with duckdb and restart the workspace.",
    "Authenticated gh CLI":
      "Install the GitHub CLI and run gh auth login in your terminal before using these tools.",
    "Authenticated gog CLI":
      "Configure gog authentication in your terminal before using email tools.",
    "CLAUZ3_SEARCH_URL backend endpoint":
      "Set CLAUZ3_SEARCH_URL to your search backend endpoint before launching the workspace.",
    "Repository access from maintainers":
      "Your Git credentials must have access to this repository before installation.",
  };
  async function post(action, payload) {
    const response = await fetch(`/api/workspace/libraries/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok)
      throw new Error(
        typeof result.detail === "string"
          ? result.detail
          : "Could not complete this library operation.",
      );
    return result;
  }
  function controls() {
    $("libraries-refresh").disabled = busy;
    $("libraries-view")
      .querySelectorAll(".library-card button, .library-card input")
      .forEach((b) => (b.disabled = busy));
    $("library-menu-toggle").disabled = busy || running();
    $("library-menu-apply").disabled =
      busy ||
      running() ||
      pending === null ||
      (sameSet(pending, state.libraries || []) && !state.librariesChanged);
    $("libraries-continue").hidden = !state.librariesChanged;
    $("libraries-continue").disabled = busy || running();
  }
  function requirements(container, values) {
    if (!values.length) return;
    const detail = text("details", "", "library-requirements");
    detail.append(text("summary", "Setup requirements"));
    for (const requirement of values)
      detail.append(
        text(
          "p",
          guidance[requirement] ||
            `Required: ${requirement}. Configure this in your terminal before using the library.`,
        ),
      );
    container.append(detail);
  }
  function mockLabel(metadata) {
    if (!metadata?.declared_mock?.length) return "";
    return metadata.kind === "mock" ? "Simulated" : "Includes simulated tools";
  }
  function mockBadge(metadata) {
    const label = mockLabel(metadata);
    if (!label) return null;
    const badge = text("span", label, "profile-badge");
    badge.title =
      "The author marks these tools as simulations. This label does not enforce isolation; review the implementation for details.";
    return badge;
  }
  function showMockDeclarations(metadata) {
    const badge = mockBadge(metadata);
    if (!badge && !metadata?.errors?.length) return;
    const panel = text("details", "", "library-mock-declarations");
    panel.append(
      text(
        "summary",
        badge ? mockLabel(metadata) : "Source inspection details",
      ),
    );
    if (badge) {
      panel.append(text("p", badge.title, "library-meta"));
      for (const name of metadata.declared_mock || [])
        panel.append(text("p", name));
    }
    for (const error of metadata.errors || [])
      panel.append(text("p", error, "library-meta"));
    $("library-review-content").append(panel);
  }
  // Domains are what chips and the message-box menu show; the source is
  // listed under the name.
  const libraryName = (library) => library.domains.join(", ");
  function libraryCard(library) {
    const isInstalled = library.installed;
    const card = text(
      "article",
      "",
      isInstalled ? "library-card" : "library-card not-installed",
    );
    const heading = text("div", "", "library-card-heading");
    heading.append(text("h2", libraryName(library)));
    if (isInstalled) {
      const toggle = document.createElement("input");
      toggle.type = "checkbox";
      toggle.setAttribute("role", "switch");
      toggle.checked = library.domains.every((d) => selected.includes(d));
      toggle.setAttribute(
        "aria-label",
        `Use ${libraryName(library)} in new conversations`,
      );
      toggle.title = "Use in new conversations";
      toggle.addEventListener("change", () =>
        saveDefault(
          toggle.checked
            ? [...new Set([...selected, ...library.domains])]
            : selected.filter((d) => !library.domains.includes(d)),
        ),
      );
      heading.append(toggle);
    } else {
      const install = text("button", "Install", "primary");
      install.setAttribute("aria-label", `Review install ${library.source}`);
      install.addEventListener("click", () =>
        prepare("install", library.source, ""),
      );
      heading.append(install);
    }
    card.append(heading);
    const origin =
      library.visibility === "bundled"
        ? "Bundled"
        : library.visibility === "global"
          ? "User-wide"
          : "Repository";
    card.append(
      text(
        "p",
        [
          library.source,
          origin,
          ...(library.dependencies.length
            ? [`needs ${library.dependencies.join(", ")}`]
            : []),
        ].join(" · "),
        "library-meta",
      ),
      text("p", library.description, "library-description"),
    );
    const tags = text("div", "", "library-tags");
    if (library.domains.some((d) => active.includes(d)))
      tags.append(text("span", "In use", "profile-badge"));
    for (const domain of library.domains) {
      const badge = isInstalled && mockBadge(mockDeclarations[domain]);
      if (badge) tags.append(badge);
    }
    if (tags.children.length) card.append(tags);
    requirements(card, library.requirements);
    const actions = text("div", "", "library-actions");
    if (isInstalled) {
      const inspect = text("button", "Details");
      inspect.setAttribute("aria-label", `Details for ${library.source}`);
      inspect.addEventListener("click", () => inspectLibrary(library));
      const update = text("button", "Review update");
      update.setAttribute("aria-label", `Review update ${library.source}`);
      update.addEventListener("click", () =>
        prepare("update", library.source, ""),
      );
      actions.append(inspect, update);
      for (const domain of library.domains) {
        const remove = text(
          "button",
          library.domains.length > 1 ? `Remove ${domain}` : "Remove",
        );
        remove.setAttribute("aria-label", `Remove ${domain}`);
        remove.addEventListener("click", () => prepare("remove", "", domain));
        actions.append(remove);
      }
    }
    if (actions.children.length) card.append(actions);
    return card;
  }
  function render() {
    const query = $("library-search").value.trim().toLowerCase();
    // Installed domains no catalogue entry provides still get a card.
    const provided = new Set(catalog.flatMap((lib) => lib.domains));
    const libraries = [
      ...catalog,
      ...installed
        .filter((d) => !provided.has(d))
        .map((domain) => ({
          source: domain,
          domains: [domain],
          dependencies: [],
          requirements: [],
          description: "Installed in this project.",
          visibility: "project",
          installed: true,
        })),
    ]
      .filter((lib) => filter === "all" || lib.installed)
      .filter((lib) =>
        [lib.source, lib.description, ...lib.domains]
          .join(" ")
          .toLowerCase()
          .includes(query),
      )
      .sort(
        (a, b) =>
          Number(b.installed) - Number(a.installed) ||
          libraryName(a).localeCompare(libraryName(b)),
      );
    const list = $("library-list");
    list.replaceChildren(...libraries.map(libraryCard));
    if (!list.children.length)
      list.append(
        text(
          "p",
          filter === "installed" && !installed.length
            ? "No libraries installed. Show all libraries to install one."
            : "No matching libraries.",
          "muted",
        ),
      );
    for (const name of ["all", "installed"])
      $(`library-filter-${name}`).setAttribute(
        "aria-pressed",
        String(filter === name),
      );
    renderMenu();
    controls();
  }
  function renderChips() {
    $("libraries").replaceChildren(
      ...(state.libraries || []).map((name) => text("li", name)),
    );
    if (!(state.libraries || []).length)
      $("libraries").append(text("li", "No libraries", "empty"));
  }
  function renderMenu() {
    if (pending === null) return;
    const list = $("library-menu-list");
    list.replaceChildren();
    const current = state.libraries || [];
    for (const domain of [...new Set([...installed, ...current])].sort()) {
      const item = text("li", "");
      const label = text("label", "");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.setAttribute("role", "switch");
      check.checked = pending.includes(domain);
      check.disabled = !installed.includes(domain);
      check.addEventListener("change", () => {
        pending = check.checked
          ? [...new Set([...pending, domain])]
          : pending.filter((d) => d !== domain);
        confirming = false;
        renderMenu();
        controls();
      });
      const was = current.includes(domain);
      label.append(text("span", domain));
      if (!installed.includes(domain))
        label.append(text("span", "not installed", "library-meta"));
      else if (check.checked !== was)
        label.append(
          text("span", check.checked ? "will be added" : "will be removed", "change"),
        );
      label.append(check);
      item.append(label);
      list.append(item);
    }
    if (!list.children.length)
      list.append(text("li", "No libraries installed yet.", "library-meta"));
    $("library-confirm").hidden = !confirming;
    $("library-menu-apply").textContent = confirming ? "Add and continue" : "Apply";
  }
  function openMenu(domains) {
    pending = [...domains];
    confirming = false;
    $("library-menu").hidden = false;
    $("library-menu-toggle").setAttribute("aria-expanded", "true");
    renderMenu();
    controls();
    load();
  }
  function closeMenu() {
    pending = null;
    confirming = false;
    $("library-menu").hidden = true;
    $("library-menu-toggle").setAttribute("aria-expanded", "false");
    controls();
  }
  async function load() {
    try {
      const response = await fetch("/api/workspace/libraries", {
        cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok)
        throw new Error(result.detail || "Could not load libraries.");
      catalog = result.catalog;
      installed = result.installed;
      active = result.active;
      mockDeclarations = result.mockDeclarations || {};
      selected = result.selected;
      render();
    } catch (error) {
      $("libraries-status").textContent = error.message;
    }
  }
  function showReview(title) {
    reviewId = null;
    $("library-review-title").textContent = title;
    $("library-review-content").replaceChildren();
    $("library-review-status").textContent = "Loading…";
    $("library-review-apply").hidden = true;
    modal.showModal();
  }
  function showApi(values) {
    const disclosure = text("details", "");
    disclosure.append(text("summary", "Tools and contracts"));
    for (const value of values) disclosure.append(text("pre", value));
    if (!values.length)
      disclosure.append(text("p", "No public API entries found."));
    $("library-review-content").append(disclosure);
  }
  async function inspectLibrary(library) {
    if (busy) return;
    busy = true;
    controls();
    showReview(library.source);
    try {
      const result = await post("inspect", { source: library.source });
      showMockDeclarations(result.mockDeclarations);
      showApi(result.api);
      $("library-review-status").textContent = "Installed copy";
    } catch (error) {
      $("library-review-status").textContent = error.message;
    } finally {
      busy = false;
      controls();
    }
  }
  async function prepare(action, source, domain) {
    if (busy) return;
    busy = true;
    controls();
    showReview(action === "remove" ? `Remove ${domain}` : source);
    $("library-review-status").textContent =
      action === "remove" ? "Preparing removal…" : "Preparing library…";
    try {
      const result = await post("prepare", { action, source, domain });
      reviewId = result.id;
      const content = $("library-review-content");
      showMockDeclarations(result.mockDeclarations);
      content.append(
        text(
          "p",
          action === "remove"
            ? "Remove this library from the project? A backup will be kept."
            : "Installing makes this code trusted in this project. Review it before continuing.",
          "view-intro",
        ),
      );
      for (const change of result.changes) {
        const item = text("div", "", "library-change");
        item.append(
          text("h3", change.domain),
          text(
            "p",
            `${change.added.length} added · ${change.changed.length} changed · ${change.removed.length} removed`,
          ),
        );
        const files = text("details", "");
        files.append(
          text("summary", "Changed files"),
          text(
            "pre",
            [
              ...change.added.map((f) => `+ ${f}`),
              ...change.changed.map((f) => `~ ${f}`),
              ...change.removed.map((f) => `− ${f}`),
            ].join("\n") || "Files already match this source.",
          ),
        );
        item.append(files);
        content.append(item);
      }
      requirements(content, result.requirements);
      if (action !== "remove") {
        showApi(result.api);
        const code = text("details", "");
        code.append(text("summary", "Implementation source"));
        for (const value of result.sourceCode) code.append(text("pre", value));
        content.append(code);
      }
      const conflict = result.domains.some((d) =>
        active.some(
          (a) => a === d || a.startsWith(d + "/") || d.startsWith(a + "/"),
        ),
      );
      $("library-review-status").textContent = conflict
        ? "Stop the current conversation before applying this change. Then review it again."
        : "";
      $("library-review-apply").textContent =
        action === "remove"
          ? "Remove library"
          : action === "update"
            ? "Update library"
            : "Install library";
      $("library-review-apply").hidden = false;
      $("library-review-apply").disabled = conflict;
    } catch (error) {
      $("library-review-status").textContent = error.message;
    } finally {
      busy = false;
      controls();
    }
  }
  async function close() {
    if (busy) return;
    busy = true;
    controls();
    modal.close();
    reviewId = null;
    try {
      await post("discard", {});
    } catch (error) {
      $("libraries-status").textContent = error.message;
    } finally {
      busy = false;
      controls();
    }
  }
  $("library-review-close").addEventListener("click", close);
  $("library-review-cancel").addEventListener("click", close);
  modal.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });
  $("library-review-apply").addEventListener("click", async () => {
    if (busy || !reviewId) return;
    busy = true;
    controls();
    $("library-review-apply").disabled = true;
    $("library-review-status").textContent = "Applying…";
    try {
      const result = await post("apply", { id: reviewId });
      modal.close();
      reviewId = null;
      $("libraries-status").textContent =
        `${result.domains.join(", ")} ${result.action === "remove" ? "removed" : "installed"}.`;
      $("libraries-status").title = result.backup
        ? `Backup: ${result.backup}`
        : "";
      await load();
    } catch (error) {
      $("library-review-status").textContent = error.message;
    } finally {
      busy = false;
      controls();
    }
  });
  async function saveDefault(domains) {
    if (busy) return;
    busy = true;
    controls();
    try {
      const result = await post("select", { domains });
      selected = result.selected;
      $("libraries-status").textContent = selected.length
        ? `New conversations start with: ${selected.join(", ")}.`
        : "New conversations start with no libraries.";
    } catch (error) {
      $("libraries-status").textContent = error.message;
    } finally {
      busy = false;
      render();
    }
  }
  for (const name of ["all", "installed"])
    $(`library-filter-${name}`).addEventListener("click", () => {
      filter = name;
      render();
    });
  $("libraries-refresh").addEventListener("click", load);
  $("library-search").addEventListener("input", render);
  $("library-menu-toggle").addEventListener("click", () =>
    pending === null ? openMenu(state.libraries || []) : closeMenu(),
  );
  $("library-menu-cancel").addEventListener("click", closeMenu);
  $("library-menu-apply").addEventListener("click", () => {
    send("clauz3/libraries", { domains: pending, confirm: confirming });
    $("library-menu-apply").disabled = true;
  });
  $("library-confirm-new").addEventListener("click", () => {
    send("clauz3/new", { domains: pending });
    closeMenu();
  });
  $("libraries-continue").addEventListener("click", () =>
    send("clauz3/libraries", { domains: state.libraries || [] }),
  );
  document.addEventListener("clauz3:confirm-libraries", (event) => {
    const { domains, added, updated } = event.detail;
    if (pending === null) openMenu(domains);
    pending = domains;
    confirming = true;
    $("library-confirm-title").textContent = added.length
      ? `Add ${added.join(", ")} to this conversation?`
      : "Continue with the updated library files?";
    if (added.length && updated)
      $("library-confirm-title").textContent += " Some library files also changed.";
    renderMenu();
    controls();
  });
  document.addEventListener("clauz3:libraries", load);
  document.addEventListener("clauz3:state", (event) => {
    const previous = state.conversationId + (state.libraries || []).join();
    state = event.detail;
    // Libraries a running conversation uses cannot be updated or removed.
    active = state.ended ? [] : state.libraries || [];
    renderChips();
    if (
      pending !== null &&
      previous !== state.conversationId + (state.libraries || []).join()
    )
      closeMenu();
    controls();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && pending !== null) closeMenu();
  });
  if (!$("libraries-view").hidden) load();
})();
