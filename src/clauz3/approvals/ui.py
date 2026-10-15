"""Shared behavior and styling for the small, server-rendered approval UI."""

SCRIPT = r"""
<script>
(() => {
  const container = document.getElementById('requests-body');
  if (!container) return;
  const status = document.getElementById('connection-status');
  const announcement = document.getElementById('announcement');
  const tabs = [...document.querySelectorAll('[data-filter]')];
  let active = 'pending';
  let refreshing = false;
  const busy = new Set();
  function filter() {
    const cards = [...container.querySelectorAll('.request-card')];
    for (const tab of tabs) {
      const count = cards.filter(c => c.dataset.status === tab.dataset.filter).length;
      tab.querySelector('.count').textContent = count;
      tab.setAttribute('aria-selected', String(tab.dataset.filter === active));
      tab.tabIndex = tab.dataset.filter === active ? 0 : -1;
    }
    for (const card of cards) {
      card.hidden = !container.dataset.single && card.dataset.status !== active;
    }
    const empty = document.getElementById('empty-state');
    if (empty) {
      empty.hidden = cards.some(c => !c.hidden);
      empty.textContent = cards.length === 0 ? 'No approval requests yet.' :
        ({pending: 'No requests awaiting approval.', approved: 'No approved requests.',
          denied: 'No rejected requests.'})[active];
    }
  }
  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener('click', () => { active = tab.dataset.filter; filter(); });
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) {
        event.preventDefault(); tabs[next].click(); tabs[next].focus();
      }
    });
  }
  async function refreshRows() {
    if (refreshing) return;
    refreshing = true;
    try {
      const response = await fetch('/ui/rows', {cache: 'no-store'});
      if (!response.ok) throw new Error(response.status === 401 ?
        'Approver session expired. Open the private login link from your terminal.' :
        'Cannot refresh requests. Retrying…');
      const template = document.createElement('template');
      template.innerHTML = await response.text();
      const incoming = [...template.content.querySelectorAll('.request-card')];
      for (const fresh of incoming.reverse()) {
        if (container.dataset.single &&
            fresh.dataset.requestId !== container.dataset.single) continue;
        const old = [...container.children].find(
          c => c.dataset.requestId === fresh.dataset.requestId);
        if (!old) { container.prepend(fresh); continue; }
        if (old.dataset.version === fresh.dataset.version ||
            busy.has(fresh.dataset.requestId)) continue;
        // Reconcile only changed records; untouched drafts and focus survive polling.
        const openPanels = [...old.querySelectorAll('details[open]')]
          .map(d => d.dataset.panel);
        for (const panel of fresh.querySelectorAll('details')) {
          panel.open = openPanels.includes(panel.dataset.panel);
        }
        old.replaceWith(fresh);
      }
      status.textContent = '';
      filter();
    } catch (error) {
      status.textContent = error.message || 'Connection lost. Retrying…';
    } finally { refreshing = false; }
  }
  container.addEventListener('click', async event => {
    const button = event.target.closest('button[data-decision]');
    if (!button) return;
    const card = button.closest('.request-card');
    const id = card.dataset.requestId;
    if (busy.has(id)) return;
    busy.add(id);
    const result = card.querySelector('.decision-result');
    const controls = card.querySelectorAll('button[data-decision]');
    controls.forEach(b => b.disabled = true);
    result.textContent = 'Saving decision…';
    try {
      const response = await fetch(`/api/requests/${encodeURIComponent(id)}/decision`, {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({decision: button.dataset.decision,
          remember: button.dataset.remember === 'true',
          feedback: card.querySelector('textarea').value})
      });
      if (!response.ok) throw new Error(response.status === 401 ?
        'Approver session expired. Reopen the private login link.' :
        `Decision was not saved (${response.status}). Please retry.`);
      result.textContent = 'Decision saved.';
      announcement.textContent = `${button.textContent.trim()}: decision saved.`;
      busy.delete(id);
      // An in-flight refresh may contain the earlier pending state. Let it finish,
      // then fetch the decision rather than replacing this card with stale markup.
      while (refreshing) await new Promise(resolve => setTimeout(resolve, 50));
      await refreshRows();
      if (card.hidden || !card.isConnected) {
        tabs.find(t => t.dataset.filter === active)?.focus();
      }
    } catch (error) {
      result.textContent = error.message || 'Decision was not saved. Please retry.';
    } finally {
      busy.delete(id);
      controls.forEach(b => b.disabled = false);
    }
  });
  filter();
  setInterval(refreshRows, 2000);
})();
</script>
"""

STYLE = """

.contract-description { margin: 0; }
.request-card .contract-details, .profile-guarantees .contract-details {
  margin: .15rem 0 .65rem;
}
.contract-details > code { display: block; margin-top: .5rem; }
    .tabs { display: flex; flex-wrap: wrap; gap: .5rem; margin: 1.5rem 0; }
    .tabs button { background: #fff; border: 1px solid #d5d7db; border-radius: 999px;
      margin: 0; padding: .65rem 1rem; cursor: pointer; font: inherit; }
    .tabs [aria-selected="true"] { color: #fff; background: #202124; border-color:
      #202124; }
    .count { margin-left: .4rem; opacity: .75; font-variant-numeric: tabular-nums; }
    .request-card { border: 1px solid #d5d7db; border-radius: 12px; padding: 1.25rem;
      margin: 1rem 0; overflow-wrap: anywhere; }
    .request-card h2 { font-size: 1.2rem; margin: 0; }
    .request-card h3 { font-size: 1rem; margin: 1.2rem 0 .4rem; }
    .request-card ul { padding-left: 1.4rem; }
    .card-heading { display: flex; justify-content: space-between; gap: 1rem;
      align-items: baseline; flex-wrap: wrap; }
    .request-link { font-size: .8rem; color: #6b7280; }
    .request-card details { margin-top: .75rem; }
    .request-card textarea { min-height: 4rem; font-family: inherit; }
    .request-card .approve { color: #fff; background: #176839; border: 1px solid
      #176839; }
    .request-card button { border: 1px solid #d5d7db; border-radius: 6px;
      background: #fff; font: inherit; cursor: pointer; }
    button:disabled { opacity: .55; cursor: wait; }
    button:focus-visible, summary:focus-visible { outline: 3px solid #2563eb;
      outline-offset: 3px; }
    .status-badge { border-radius: 999px; padding: .15rem .6rem; background: #f7f8fa;
      font-size: .85rem; }
    [data-status="pending"] .status-badge { background: #fff5dc; color: #745000; }
    [data-status="approved"] .status-badge { background: #eaf6ee; color: #176839; }
    [data-status="denied"] .status-badge { background: #fdeff0; color: #a3202a; }
    .decision-result, #connection-status { color: #745000; }
    .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path:
      inset(50%); }
    [hidden] { display: none !important; }
"""
