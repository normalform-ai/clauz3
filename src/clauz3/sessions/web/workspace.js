/* ACP updates plus server-owned Clauz3 run state. Agent text is always inert. */
(() => {
  const $ = (id) => document.getElementById(id);
  const log = $("conversation");
  let initialView = true;
  let conversations = [],
    historyBusy = false,
    historySignature = "";
  const welcome = $("welcome").cloneNode(true);
  const turns = new Map(),
    tools = new Map(),
    runs = new Map();
  const historicalApprovals = new Map();
  const approvals = new Map(),
    deciding = new Set();
  let socket,
    state = {},
    currentTurn,
    assistant,
    connected = false,
    replaying = false;
  let draftKey = "clauz3-draft",
    pollBusy = false,
    retry;
  const storage = {
    get(key) {
      try {
        return sessionStorage.getItem(key) || "";
      } catch {
        return "";
      }
    },
    set(key, value) {
      try {
        sessionStorage.setItem(key, value);
      } catch {}
    },
  };
  const text = (tag, value, cls) => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (cls) node.className = cls;
    return node;
  };
  function notice(value) {
    $("notice").textContent = value;
    $("notice").hidden = !value;
  }
  function send(method, params = {}) {
    if (!connected) return notice("Connection lost. Reconnecting…");
    socket.send(JSON.stringify({ method, params }));
  }
  function controls() {
    $("copy-handoff").disabled =
      !state.conversationId || Boolean($("copy-handoff").dataset.copying);
    $("conversation-rename-current").disabled = !state.conversationId;
    const active = (state.runs || []).some(
      (r) => !["completed", "refused", "failed", "cancelled"].includes(r.phase),
    );
    const ready =
      connected && state.sessionId && !state.ended && !state.stopping;
    $("prompt").disabled = !ready || state.historyLimited;
    $("send").disabled = !ready || state.busy || active || state.historyLimited;
    $("stop").disabled = !ready;
    $("agent-select").disabled =
      !connected || state.busy || state.stopping || Boolean(state.turn);
    $("new").disabled =
      !connected ||
      state.busy ||
      active ||
      state.stopping ||
      (!state.sessionId && !state.ended);
    // Switching waits for the agent, but returning to the current
    // conversation never does: it is only a change of view.
    const switching = !connected || state.busy || active || state.stopping;
    $("conversation-list")
      .querySelectorAll("button")
      .forEach((b) => {
        b.disabled = switching && !b.hasAttribute("aria-current");
        if (b.classList.contains("conversation-link"))
          b.title = b.disabled
            ? "Finish or stop the current action to switch conversations"
            : b.dataset.label;
      });
    const waitingCount = (state.runs || []).filter(
      (r) => r.phase === "awaiting_approval",
    ).length;
    const waiting = waitingCount > 0;
    $("approval-count").textContent = String(waitingCount);
    $("approval-count").hidden = !waiting;
    $("approvals-waiting").hidden =
      !waiting || location.pathname !== "/ui/profile";
    $("approvals-waiting-text").textContent =
      waitingCount === 1
        ? "An action in this conversation is waiting for your approval."
        : `${waitingCount} actions in this conversation are waiting for your approval.`;
    $("status").textContent = !connected
      ? "Reconnecting…"
      : state.stopping
        ? "Stopping conversation…"
        : state.ended
          ? state.resumeError
            ? "Conversation unavailable"
            : "Conversation stopped"
          : waiting
            ? "Waiting for your approval"
            : active
              ? "Action in progress"
              : state.busy
                ? `${state.agentLabel || "Agent"} is working…`
                : state.historyLimited
                  ? "Transcript storage full · start a new conversation"
                  : state.sessionId
                    ? "Ready"
                    : `Starting ${state.agentLabel || "agent"}…`;
    $("resume-recovery").hidden = !state.resumeError && !state.librariesChanged;
    $("approvals-paused").hidden = !state.approvalsPaused;
    $("resume-detail").textContent = state.resumeError || "";
    $("resume-detail").closest("details").hidden = !state.resumeError;
    $("resume-new").disabled = $("new").disabled;
    if (state.error) notice(state.error);
    else if (state.historyLimited)
      notice(
        "Transcript storage is full. The current action can finish; later output is not saved. Start a new conversation to continue.",
      );
  }
  function turnNode(number) {
    if (!turns.has(number)) {
      $("welcome")?.remove();
      const node = document.createElement("section");
      node.className = "turn";
      node.dataset.turn = number;
      turns.set(number, node);
      log.append(node);
    }
    return turns.get(number);
  }
  function display(message) {
    const p = message.params || {};
    if (message.method === "clauz3/workspace_changed") {
      // A full page refresh drops old file previews, approval polls and drafts
      // from the display. The server already owns the new project and lease.
      location.assign("/ui");
      return;
    }
    if (message.method === "clauz3/confirm_workspace") {
      document.dispatchEvent(new CustomEvent("clauz3:confirm-workspace", { detail: p }));
      return;
    }
    if (message.method === "clauz3/conversations") {
      conversations = p.conversations;
      renderHistory();
      return;
    }
    if (message.method === "clauz3/restore") {
      notice("");
      refreshHistory();
      refreshApprovalHistory();
      log.replaceChildren(welcome.cloneNode(true));
      turns.clear();
      tools.clear();
      runs.clear();
      currentTurn = null;
      assistant = null;
      state = p;
      replaying = true;
      for (const item of p.history || []) display(item);
      replaying = false;
      applyState(p);
      if (initialView && p.showLibraries && location.pathname === "/ui")
        navigate("/ui/libraries");
      initialView = false;
      return;
    }
    if (message.method === "clauz3/state") {
      applyState(p);
      return;
    }
    if (message.method === "clauz3/notice") {
      document.dispatchEvent(new CustomEvent("clauz3:workspace-notice", { detail: p.text }));
      notice(p.text);
      return;
    }
    if (message.method === "clauz3/confirm_libraries") {
      document.dispatchEvent(
        new CustomEvent("clauz3:confirm-libraries", { detail: p }),
      );
      return;
    }
    if (message.method === "clauz3/libraries_changed") {
      assistant = null;
      const changes = [
        ...p.added.map((name) => `+ ${name}`),
        ...p.removed.map((name) => `− ${name}`),
        ...(p.updated ? ["library files updated"] : []),
        ...(p.paused ? ["automatic approvals paused"] : []),
      ];
      turnNode(p.turn || 1).append(
        text("p", `Libraries changed: ${changes.join(" · ")}`, "library-marker"),
      );
      return;
    }
    if (message.method === "clauz3/user") {
      currentTurn = turnNode(p.turn);
      assistant = null;
      const message = text("div", "", "user-message");
      const actions = text("div", "", "message-actions");
      actions.append(clauz3Copy.button("Copy message", () => p.text));
      message.append(text("div", p.text), actions);
      currentTurn.append(message);
      if (!replaying && $("prompt").value === p.text) {
        $("prompt").value = "";
        storage.set(draftKey, "");
      }
      return;
    }
    if (message.method === "clauz3/turn_end") {
      assistant = null;
      return;
    }
    if (message.method !== "session/update") return;
    const u = p.update || {};
    // Setup metadata is not a visible turn and must not hide the welcome view.
    if (
      !["agent_message_chunk", "tool_call", "tool_call_update", "plan"].includes(
        u.sessionUpdate,
      )
    )
      return;
    currentTurn ||= turnNode(state.turn || 1);
    if (
      u.sessionUpdate === "agent_message_chunk" &&
      u.content?.type === "text"
    ) {
      if (!assistant) {
        assistant = text("div", "", "assistant-message");
        assistant.source = "";
        assistant.content = text("div", "", "markdown");
        const message = assistant;
        const actions = text("div", "", "message-actions");
        actions.append(clauz3Copy.button("Copy response", () => message.source));
        assistant.append(assistant.content, actions);
        currentTurn.append(assistant);
      }
      assistant.source += u.content.text || "";
      assistant.content.replaceChildren(clauz3Markdown.render(assistant.source));
    } else if (
      u.sessionUpdate === "tool_call" ||
      u.sessionUpdate === "tool_call_update"
    ) {
      assistant = null;
      const toolKey = `${currentTurn.dataset.turn}:${u.toolCallId}`;
      let item = tools.get(toolKey);
      if (!item) {
        const node = document.createElement("details");
        node.className = "tool";
        const summary = text("summary", "Tool");
        const body = text("div", "", "tool-body");
        node.append(summary, body);
        currentTurn.append(node);
        item = { node, summary, body, data: {} };
        tools.set(toolKey, item);
      }
      for (const [key, value] of Object.entries(u))
        if (value !== null) item.data[key] = value;
      clauz3ToolCards.render(item, item.data, state.unrestricted);
    } else if (u.sessionUpdate === "plan") {
      let plan = currentTurn.querySelector(".plan");
      if (!plan) {
        plan = document.createElement("ul");
        plan.className = "plan";
        currentTurn.append(plan);
      }
      plan.replaceChildren(
        ...(u.entries || []).map((e) =>
          text("li", `${e.content} · ${e.status.replaceAll("_", " ")}`),
        ),
      );
    }
  }
  const phases = {
    checking: "Checking program",
    proving: "Proving guarantees",
    submitting_approval: "Submitting for approval",
    awaiting_approval: "Your approval needed",
    executing: "Executing approved action",
    completed: "Completed",
    refused: "Did not execute",
    failed: "Failed",
    cancelled: "Stopped",
  };
  function applyState(next) {
    state = next;
    renderHistory();
    const name =
      (state.cwd || "").split("/").filter(Boolean).pop() || "Your project";
    $("project-path").textContent = state.cwd || "";
    $("workspace-picker").title = state.cwd || "";
    $("project-name").textContent = name;
    $("workspace-avatar").textContent = name.slice(0, 2);
    $("model").textContent = `${state.agentLabel || "Claude"} · ${state.model || "default"}`;
    $("prompt-label").textContent = `Message ${state.agentLabel || "agent"}`;
    $("unrestricted-mode").hidden = !state.unrestricted;
    $("confinement").textContent = state.unrestricted
      ? "Unrestricted: native tools can bypass proofs and approval."
      : "Your approval controls effects.";
    document
      .querySelector(".sidebar-bottom")
      .classList.toggle("unrestricted", Boolean(state.unrestricted));
    const selector = $("agent-select");
    if (selector.dataset.agent !== state.agent) {
      selector.replaceChildren(...(state.providers || []).map((provider) => {
        const option = text("option", `${provider.label}${provider.available ? "" : " (not installed)"}${provider.modeHint ? ` (${provider.modeHint})` : ""}`);
        option.value = provider.id;
        option.disabled = !provider.available || !provider.enabled;
        return option;
      }));
      selector.value = state.agent || "claude";
      selector.dataset.agent = state.agent;
    }
    selector.hidden = !(state.providers || []).length;
    // An agent is chosen before the first message; afterwards it is fixed.
    selector.title = [
      state.turn
        ? "Start a new conversation to use a different agent."
        : "Agent for this conversation",
      ...(state.providers || [])
        .filter((p) => !p.available)
        .map((p) => `${p.label}: ${p.install}`),
    ].join("\n");
    const key = `clauz3-draft:${state.cwd}:${state.conversationId}`;
    if (draftKey !== key) {
      draftKey = key;
      $("prompt").value = storage.get(key);
    }
    for (const run of state.runs || []) {
      let item = runs.get(run.run_id);
      if (!item) {
        const node = text("article", "", "run");
        node.dataset.runId = run.run_id;
        const heading = text("div", "", "run-heading");
        const badge = text("span", "", "phase");
        heading.append(text("strong", "Action review"), badge);
        const summary = text("p", "", "run-summary");
        const reason = text("p", "", "run-reason");
        const diagnostics = text("details", "", "run-diagnostics");
        const detail = text("pre", "", "run-diagnostic");
        diagnostics.append(text("summary", "Run diagnostics"), detail);
        node.append(heading, summary, reason, diagnostics);
        turnNode(run.turn || 1).append(node);
        item = { node, badge, summary, reason, diagnostics, detail };
        runs.set(run.run_id, item);
      }
      item.badge.textContent = phases[run.phase] || run.phase;
      item.badge.className =
        "phase" +
        (run.phase === "awaiting_approval"
          ? " waiting"
          : ["failed", "refused"].includes(run.phase)
            ? " failed"
            : "");
      const status = window.clauz3RunStatus(run);
      item.summary.textContent = status.summary;
      item.reason.textContent = status.reason;
      item.reason.hidden = !status.reason;
      item.detail.textContent = status.diagnostics;
      item.diagnostics.hidden = !status.diagnostics;
      if (run.request_id && approvals.has(run.request_id))
        attachApproval(item, approvals.get(run.request_id), run);
      else if (
        historicalApprovals.has(run.request_id) &&
        !item.node.querySelector(".request-card")
      ) {
        const event = historicalApprovals.get(run.request_id);
        const card = text("div", "", "request-card");
        // A request may have several decisions over time. Only show its
        // immutable request details here; the profile lists the dated decisions.
        card.append(text("h3", "Saved approval request"));
        const guarantees = window.clauz3RenderGuarantees(event.record);
        const source = text("details", "");
        source.append(
          text("summary", "Show source"),
          text("pre", event.record.program || ""),
        );
        card.append(text("h3", "Verified limits"), guarantees, source);
        const link = text("a", "View approval profile and history");
        link.href = "/ui/profile";
        card.append(link);
        item.node.append(card);
      }
    }
    controls();
    document.dispatchEvent(new CustomEvent("clauz3:state", { detail: state }));
  }
  function attachApproval(item, fresh, run) {
    let old = item.node.querySelector(".request-card");
    if (
      !old ||
      (old.dataset.version !== fresh.dataset.version &&
        !deciding.has(run.request_id))
    ) {
      const next = fresh.cloneNode(true);
      next.hidden = false;
      if (old) {
        const opened = [...old.querySelectorAll("details[open]")].map(
          (d) => d.dataset.panel,
        );
        for (const panel of next.querySelectorAll("details"))
          panel.open = opened.includes(panel.dataset.panel);
        const draft = old.querySelector("textarea")?.value;
        if (draft && next.querySelector("textarea"))
          next.querySelector("textarea").value = draft;
        old.replaceWith(next);
      } else item.node.append(next);
      old = next;
    }
    const stopped =
      ["completed", "refused", "failed", "cancelled"].includes(run.phase) ||
      state.ended ||
      state.stopping;
    for (const button of old.querySelectorAll("[data-decision]"))
      button.disabled = stopped || deciding.has(run.request_id);
    if (stopped && old.querySelector(".pending"))
      old.querySelector(".pending").textContent =
        "This action is no longer running.";
  }
  async function refreshApprovalHistory() {
    try {
      const response = await fetch("/api/profile", { cache: "no-store" });
      if (!response.ok) return;
      for (const event of (await response.json()).history || []) {
        if (
          event.record?.request_id &&
          !historicalApprovals.has(event.record.request_id)
        )
          historicalApprovals.set(event.record.request_id, event);
      }
      applyState(state);
    } catch {
      /* Live request polling reports connection failures. */
    }
  }
  async function pollApprovals() {
    if (pollBusy || document.hidden) return;
    pollBusy = true;
    try {
      const response = await fetch("/ui/rows", { cache: "no-store" });
      if (!response.ok)
        throw new Error(
          "Cannot load approvals. Reopen the private terminal link if your login expired.",
        );
      const template = document.createElement("template");
      template.innerHTML = await response.text();
      for (const card of template.content.querySelectorAll(".request-card"))
        approvals.set(card.dataset.requestId, card);
      applyState(state);
    } catch (error) {
      notice(error.message);
    } finally {
      pollBusy = false;
    }
  }
  log.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-decision]");
    if (!button) return;
    const card = button.closest(".request-card"),
      id = card.dataset.requestId;
    if (deciding.has(id)) return;
    deciding.add(id);
    applyState(state);
    const result = card.querySelector(".decision-result");
    result.textContent = "Saving decision…";
    try {
      const response = await fetch(
        `/api/requests/${encodeURIComponent(id)}/decision`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            decision: button.dataset.decision,
            remember: button.dataset.remember === "true",
            feedback: card.querySelector("textarea")?.value || "",
          }),
        },
      );
      if (!response.ok)
        throw new Error(
          `Decision was not saved (${response.status}). Please retry.`,
        );
      result.textContent = "Decision saved.";
    } catch (error) {
      result.textContent = error.message;
    } finally {
      deciding.delete(id);
      await pollApprovals();
    }
  });
  function connect() {
    socket = new WebSocket(`ws://${location.host}/api/chat/acp`);
    socket.onopen = () => {
      connected = true;
      notice("");
      controls();
    };
    socket.onmessage = (event) => {
      const nearBottom =
        window.innerHeight + window.scrollY >= document.body.scrollHeight - 180;
      display(JSON.parse(event.data));
      if (nearBottom)
        window.scrollTo({
          top: document.body.scrollHeight,
          behavior: "instant",
        });
    };
    socket.onclose = (event) => {
      connected = false;
      controls();
      if (event.code === 1008) {
        notice("Open the private login link from your terminal to sign in.");
        return;
      }
      if (event.code === 1013)
        notice(
          "This workspace is open in another tab. Reconnecting when it closes…",
        );
      clearTimeout(retry);
      retry = setTimeout(connect, event.code === 1013 ? 3000 : 1500);
    };
  }
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!$("send").disabled) {
      notice("");
      send("session/prompt", { prompt: $("prompt").value });
    }
  });
  $("prompt").addEventListener("input", () =>
    storage.set(draftKey, $("prompt").value),
  );
  $("prompt").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("composer").requestSubmit();
    }
  });
  $("stop").addEventListener("click", () => send("session/cancel"));
  async function handoff(conversationId) {
    const response = await fetch("/api/chat/handoff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversationId }),
    });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(result.detail || "Could not save the conversation snapshot");
    }
    const result = await response.json();
    return result.text;
  }
  clauz3Copy.bind($("copy-handoff"), () => handoff(state.conversationId));
  $("new").addEventListener("click", () => {
    notice("");
    navigate("/ui");
    send("clauz3/new", { agent: state.agent || "claude" });
  });
  $("agent-select").addEventListener("change", () => {
    // The server replaces an unused conversation rather than adding another.
    notice("");
    send("clauz3/new", {
      agent: $("agent-select").value,
      domains: state.libraries || [],
    });
  });
  $("resume-new").addEventListener("click", () => {
    notice("");
    navigate("/ui");
    send("clauz3/new", {
      agent: state.agent || "claude",
      domains: state.libraries || [],
    });
  });
  log.addEventListener("click", (event) => {
    if (event.target.closest(".suggestions button")) {
      $("prompt").value = event.target.textContent;
      $("prompt").focus();
    }
  });
  let menuChat = null;
  function startRename(id) {
    const item = $("conversation-list").querySelector(`li[data-id="${CSS.escape(id)}"]`);
    const chat = conversations.find((c) => c.id === id);
    if (!item || !chat) return;
    const form = document.createElement("form");
    form.className = "rename-form";
    const input = document.createElement("input");
    input.value = chat.title;
    input.maxLength = 120;
    input.required = true;
    input.setAttribute("aria-label", "Conversation title");
    const save = text("button", "Save");
    save.type = "submit";
    form.append(input, save);
    item.replaceChildren(form);
    input.focus();
    input.select();
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      send("clauz3/rename", { id: chat.id, title: input.value });
      $("history-search").focus();
      historySignature = "";
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        $("history-search").focus();
        historySignature = "";
        renderHistory();
      }
    });
  }
  function renderHistory() {
    const query = $("history-search").value.toLowerCase();
    const signature = JSON.stringify([
      conversations,
      state.conversationId,
      query,
    ]);
    const current = conversations.find((c) => c.id === state.conversationId);
    $("conversation-title").textContent =
      current?.title === "New conversation" || !current
        ? "New conversation"
        : current.title;
    if (
      signature === historySignature ||
      ($("conversation-list").contains(document.activeElement) &&
        document.activeElement.tagName === "INPUT")
    )
      return;
    historySignature = signature;
    const list = $("conversation-list");
    list.replaceChildren();
    const matches = conversations.filter((c) =>
      c.title.toLowerCase().includes(query),
    );
    for (const group of clauz3History.group(matches)) {
      list.append(text("li", group.label, "history-group"));
      for (const chat of group.conversations) {
        const item = text("li", "");
        item.dataset.id = chat.id;
        const button = text("button", chat.title, "conversation-link");
        button.dataset.label = `${chat.title} · ${new Date(chat.updated * 1000).toLocaleString()}`;
        button.title = button.dataset.label;
        if (chat.id === state.conversationId)
          button.setAttribute("aria-current", "true");
        button.addEventListener("click", () => {
          navigate("/ui");
          if (chat.id !== state.conversationId)
            send("clauz3/select", { id: chat.id });
        });
        const more = text("button", "", "conversation-more");
        more.innerHTML = '<svg class="icon"><use href="#icon-dots" /></svg>';
        more.setAttribute("aria-label", `Actions for ${chat.title}`);
        more.setAttribute("aria-haspopup", "menu");
        more.setAttribute("aria-expanded", "false");
        more.addEventListener("click", () => {
          menuChat = chat.id;
          clauz3Menu.toggle($("conversation-menu"), more);
        });
        item.append(button, more);
        list.append(item);
      }
    }
    if (!matches.length && query)
      list.append(text("li", "No matching conversations", "history-empty"));
    controls();
  }
  $("approvals-waiting-review").addEventListener("click", () => {
    navigate("/ui");
    log
      .querySelector(".phase.waiting")
      ?.closest(".run")
      ?.scrollIntoView({ block: "center" });
  });
  $("conversation-menu-rename").addEventListener("click", () => {
    $("conversation-menu").hidePopover();
    startRename(menuChat);
  });
  clauz3Copy.bind($("conversation-menu-handoff"), () => handoff(menuChat));
  $("conversation-actions-toggle").addEventListener("click", (event) =>
    clauz3Menu.toggle($("conversation-actions"), event.currentTarget),
  );
  $("conversation-rename-current").addEventListener("click", () => {
    $("conversation-actions").hidePopover();
    startRename(state.conversationId);
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "k") {
      event.preventDefault();
      $("history-search").focus();
    }
  });
  async function refreshHistory() {
    if (historyBusy) return;
    historyBusy = true;
    try {
      const response = await fetch("/api/chat/conversations", {
        cache: "no-store",
      });
      if (!response.ok) throw new Error("Could not load conversation history.");
      conversations = (await response.json()).conversations;
      renderHistory();
      controls();
    } catch (error) {
      notice(error.message);
    } finally {
      historyBusy = false;
    }
  }
  function showView() {
    const profile = location.pathname === "/ui/profile";
    const libraries = location.pathname === "/ui/libraries";
    $("chat-view").hidden = profile || libraries;
    $("approvals-view").hidden = !profile;
    $("libraries-view").hidden = !libraries;
    controls();
    // The chat view is shown by the selected conversation, not a nav link.
    document
      .querySelectorAll(".sidebar nav a")
      .forEach((a) =>
        a.classList.toggle(
          "selected",
          a.getAttribute("href") ===
            (profile ? "/ui/profile" : libraries ? "/ui/libraries" : "/ui"),
        ),
      );
    if (profile || libraries) {
      if (!$("files-panel").hidden) $("files-close").click();
      document.dispatchEvent(
        new Event(profile ? "clauz3:approvals" : "clauz3:libraries"),
      );
    }
  }
  function navigate(path) {
    if (location.pathname !== path) history.pushState({}, "", path);
    showView();
  }
  document.addEventListener("click", (event) => {
    const link = event.target.closest(
      'a[href="/ui/profile"], a[href="/ui/libraries"]',
    );
    if (link && !event.metaKey && !event.ctrlKey && !event.shiftKey) {
      event.preventDefault();
      navigate(link.getAttribute("href"));
    }
  });
  document.addEventListener("clauz3:send", (event) => {
    notice("");
    navigate("/ui");
    const params = { ...event.detail.params };
    if (event.detail.method === "clauz3/new") {
      params.agent = state.agent || "claude";
    }
    send(event.detail.method, params);
  });
  window.addEventListener("popstate", showView);
  $("history-search").addEventListener("input", renderHistory);
  showView();
  connect();
  refreshHistory();
  setInterval(refreshHistory, 2000);
  pollApprovals();
  setInterval(pollApprovals, 1000);
})();
