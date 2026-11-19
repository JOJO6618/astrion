export function conversationKey(row) {
  return JSON.stringify([row.conversation_kind, row.workspace_id, row.conversation_id]);
}

export function shouldShowConversationNotification(focus, row) {
  return focus.kind === 'external' || (
    focus.kind === 'detached' && focus.conversationKey !== conversationKey(row)
  );
}

/** Compare successful snapshots only. A failed poll cannot look like an idle conversation. */
export function createConversationActivityTracker() {
  let previous = new Map();
  let initialized = false;

  return (rows) => {
    const next = new Map();
    const notices = [];
    for (const row of rows) {
      const key = conversationKey(row);
      const before = previous.get(key);
      const sameRun = before && before.runId === row.run_id;
      const current = {
        runId: row.run_id,
        notified: sameRun ? before.notified : false,
        idlePolls: sameRun ? before.idlePolls : 0,
        pending: {},
      };
      next.set(key, current);
      for (const type of ['approval', 'question']) {
        const ids = row.pending?.[type] ?? [];
        const seen = before?.pending[type] ?? new Set();
        if (ids.some((id) => !seen.has(id))) notices.push({ row, type });
        current.pending[type] = new Set([...seen, ...ids]);
      }
      if (row.running || row.run_status !== 'succeeded') {
        current.idlePolls = 0;
        continue;
      }
      // Seed historical completions on startup. Require a second idle sample to
      // bridge handoffs from a finished task to its background follow-up.
      if (!initialized) current.notified = true;
      if (!current.notified && ++current.idlePolls >= 2) {
        current.notified = true;
        notices.push({ row, type: 'completed' });
      }
    }
    previous = next;
    initialized = true;
    return notices;
  };
}

/** Window bounds and visible capacity use the same geometry as the stack CSS. */
export function notificationBounds(area, count) {
  const screenInset = 12;
  const shadowInset = 12;
  const rowHeight = 68;
  const gap = 8;
  const available = Math.max(1, Math.floor((area.height - 2 * (screenInset + shadowInset) + gap) / (rowHeight + gap)));
  const visible = Math.min(count, available, 4);
  const width = Math.min(356 + 2 * shadowInset, area.width - screenInset);
  const height = 2 * shadowInset + visible * rowHeight + Math.max(0, visible - 1) * gap;
  // Align the viewport to the edge so it clips the banner as it slides in.
  return { visible, bounds: { x: area.x + area.width - width, y: area.y + screenInset, width, height } };
}
