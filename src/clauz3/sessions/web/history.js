/* Group conversations by recency, newest first, as chat apps do. */
globalThis.clauz3History = {
  group(conversations, now = new Date()) {
    const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
    const day = 86400;
    const bounds = [
      ["Today", midnight],
      ["Yesterday", midnight - day],
      ["Previous 7 days", midnight - 7 * day],
      ["Previous 30 days", midnight - 30 * day],
      ["Older", -Infinity],
    ];
    const groups = bounds.map(([label]) => ({ label, conversations: [] }));
    for (const chat of [...conversations].sort((a, b) => b.updated - a.updated)) {
      groups[bounds.findIndex(([, start]) => chat.updated >= start)].conversations.push(chat);
    }
    return groups.filter((g) => g.conversations.length);
  },
};
