/* Workspace selection belongs to the operator, never the agent. */
(() => {
  const $ = id => document.getElementById(id);
  const menu = $("workspace-menu");
  const dialog = $("workspace-confirm");
  let snapshot = {}, pending = null, busy = false;
  const emailPrompt = 'Use the installed mock email library to send bob@example.com the message "Hello from ClauZ3". Guarantee that only Bob can be emailed, there is at most one email, and recipients are unique. Submit it for approval.';
  function status(message) { $("workspace-status").textContent = message; }
  function setBusy(value) {
    busy = value;
    menu.querySelectorAll("button, input").forEach(button => { button.disabled = value || button.dataset.unavailable === "true"; });
    $("workspace-confirm-yes").disabled = value;
  }
  function request(params) {
    if (busy) return;
    setBusy(true);
    if (menu.matches(":popover-open")) menu.hidePopover();
    if (dialog.open) dialog.close();
    status("Opening workspace…");
    document.dispatchEvent(new CustomEvent("clauz3:send", { detail: { method: "clauz3/workspace", params } }));
  }
  function confirm(params, message, label) {
    pending = params;
    setBusy(false);
    if (menu.matches(":popover-open")) menu.hidePopover();
    $("workspace-confirm-text").textContent = message;
    $("workspace-confirm-yes").textContent = label;
    status("");
    if (!dialog.open) dialog.showModal();
  }
  function showTutorial() {
    $("workspace-picker").dataset.tutorial = String(Boolean(snapshot.tutorial));
    if (snapshot.tutorial) {
      $("project-name").textContent = "Email approvals";
      $("workspace-avatar").textContent = "✉";
    }
    document.querySelectorAll(".try-tutorial").forEach(button => { button.hidden = snapshot.tutorial || !snapshot.enabled; });
  }
  async function load() {
    try {
      const response = await fetch("/api/workspaces", { cache: "no-store" });
      if (!response.ok) throw new Error("Could not load workspaces. Reopen the private login link if needed.");
      snapshot = await response.json();
      $("workspace-picker").hidden = !snapshot.enabled;
      $("tutorial-help").hidden = !snapshot.tutorial;
      $("workspace-reset").hidden = !snapshot.tutorial;
      showTutorial();
      const items = (snapshot.recent || []).map(item => {
        const li = document.createElement("li");
        const button = document.createElement("button");
        button.setAttribute("role", "menuitem");
        button.title = item.path;
        button.disabled = !item.available;
        button.dataset.unavailable = String(!item.available);
        const avatar = document.createElement("span");
        avatar.className = "workspace-avatar";
        avatar.textContent = item.name.slice(0, 2);
        const label = document.createElement("span");
        label.className = "menu-label";
        label.textContent = item.name;
        const path = document.createElement("small");
        path.textContent = item.available ? item.path : "Folder not found";
        label.append(path);
        button.append(avatar, label);
        if (item.path === snapshot.current) {
          button.setAttribute("aria-current", "true");
          const check = document.createElement("span");
          check.className = "menu-check";
          check.textContent = "✓";
          button.append(check);
        }
        button.addEventListener("click", () => request({ path: item.path }));
        li.append(button);
        return li;
      });
      $("workspace-recents").replaceChildren(...items);
    } catch (error) { status(error.message); }
  }
  async function open() {
    pending = null;
    status("");
    setBusy(false);
    $("workspace-open").hidden = true;
    clauz3Menu.open(menu, $("workspace-picker"));
    await load();
  }
  $("workspace-picker").addEventListener("click", () => {
    if (menu.matches(":popover-open")) menu.hidePopover();
    else open();
  });
  $("workspace-open-toggle").addEventListener("click", () => {
    $("workspace-open").hidden = false;
    $("workspace-folder").focus();
  });
  $("workspace-open").addEventListener("submit", event => {
    event.preventDefault();
    request({ path: $("workspace-folder").value.trim() });
  });
  $("workspace-email").addEventListener("click", () => request({ tutorial: "email" }));
  $("workspace-confirm-yes").addEventListener("click", () => request(pending));
  $("workspace-confirm-no").addEventListener("click", () => { pending = null; dialog.close(); });
  const reset = () => confirm(
    { tutorial: "email", reset: true, confirmReset: true, stop: true },
    "Reset the email tutorial? This stops its active work and replaces its files, conversations, and remembered approvals. Other workspaces are unchanged.",
    "Reset tutorial",
  );
  $("tutorial-reset").addEventListener("click", reset);
  $("workspace-reset").addEventListener("click", reset);
  document.addEventListener("clauz3:confirm-workspace", event => confirm(
    { ...event.detail, stop: true },
    "This workspace has active work. Stop its agent and workers before opening the selected workspace? Its conversation history will be saved.",
    "Stop and switch",
  ));
  document.addEventListener("clauz3:workspace-notice", event => {
    if (busy) { setBusy(false); status(event.detail); }
  });
  document.addEventListener("click", event => {
    if (event.target.closest(".try-tutorial")) request({ tutorial: "email" });
  });
  $("tutorial-prompts").addEventListener("click", event => {
    const kind = event.target.closest("button")?.dataset.prompt;
    if (!kind) return;
    const prompt = kind === "send" ? emailPrompt : kind === "reject"
      ? 'Prepare another mock email to alice@example.com with only Alice allowed and at most one email. Submit it for approval; I want to practice rejecting it.'
      : 'Prepare another mock email to bob@example.com, with only Bob allowed, at most one email, and unique recipients. Submit it for approval so I can try Approve and remember.';
    $("prompt").value = prompt;
    $("prompt").dispatchEvent(new Event("input", { bubbles: true }));
    $("prompt").focus();
  });
  // Restoring a conversation recreates the welcome panel and resets the name.
  document.addEventListener("clauz3:state", showTutorial);
  load();
})();
