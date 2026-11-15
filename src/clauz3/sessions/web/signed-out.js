/* One signed-out state for a tab whose approver session is no longer valid.
 * Each server start mints a new sign-in key, so a tab left open across a
 * restart keeps a stale cookie: every authenticated request returns 401 and
 * the chat socket closes with 1008. Rather than leave the old conversation
 * and its approval cards looking usable, the whole page is replaced. */
globalThis.clauz3SignedOut = (() => {
  const heading = "This tab is signed out";
  const paragraphs = [
    [
      "This usually happens after the clauz3 server restarts, which changes its private sign-in link.",
    ],
    [
      "Run ",
      { code: "clauz3 login-url" },
      " in a terminal on this computer and open the link it prints, or find the link where the server was started.",
    ],
    [
      "Actions waiting for your approval are still waiting, but they cannot be approved from this tab until you sign in again.",
    ],
  ];
  let active = false;
  const api = {
    get active() {
      return active;
    },
    // 401 is only used for a missing or invalid approver credential; 403
    // means a request was refused (an origin or a library), not signed out.
    response: (status) => status === 401,
    close: (code) => code === 1008,
    enter(doc = globalThis.document) {
      if (active) return;
      active = true;
      const page = doc.createElement("main");
      page.className = "signed-out";
      page.setAttribute("role", "alert");
      const title = doc.createElement("h1");
      title.textContent = heading;
      page.append(title);
      for (const parts of paragraphs) {
        const paragraph = doc.createElement("p");
        for (const part of parts) {
          if (typeof part === "string") paragraph.append(part);
          else {
            const code = doc.createElement("code");
            code.textContent = part.code;
            paragraph.append(code);
          }
        }
        page.append(paragraph);
      }
      doc.title = "Clauz3 · Signed out";
      doc.body.replaceChildren(page);
    },
    // Every script's requests go through fetch, so one wrapper covers them all.
    watch(fetcher) {
      return async (...args) => {
        const result = await fetcher(...args);
        if (api.response(result.status)) api.enter();
        return result;
      };
    },
  };
  if (globalThis.document && globalThis.fetch)
    globalThis.fetch = api.watch(globalThis.fetch.bind(globalThis));
  return api;
})();
