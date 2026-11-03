import {cardKey} from './favorites.js';

// Multi-select on the favorites page. Bulk actions run one card at a time through the same
// favorites / subscriptions models, so per-card busy state and error notes still apply.
export function createSelection(favorites, subscriptions, onChange) {
  let generation = 0;
  const state = {active: false, keys: new Set(), running: false, done: 0, total: 0, failed: 0};
  const selected = () => favorites.state.records.filter(row => state.keys.has(cardKey(row.item)));
  function clear() {
    generation++;
    Object.assign(state, {active: false, keys: new Set(), running: false, done: 0, total: 0, failed: 0});
    onChange();
  }
  function setActive(active) {
    if (state.running) return;
    Object.assign(state, {active, keys: new Set(), failed: 0}); onChange();
  }
  function toggle(item) {
    if (!state.active || state.running) return;
    const key = cardKey(item);
    if (!state.keys.delete(key)) state.keys.add(key);
    onChange();
  }
  function setAll(items, on) {
    if (!state.active || state.running) return;
    for (const item of items) on ? state.keys.add(cardKey(item)) : state.keys.delete(cardKey(item));
    onChange();
  }
  // Called while rendering: keep only cards still shown, so a bulk action never touches
  // a card hidden by the current group or filters.
  function prune(items) {
    const shown = new Set(items.map(cardKey));
    for (const key of state.keys) if (!shown.has(key)) state.keys.delete(key);
  }
  // Failed cards stay selected so the same action can be retried.
  async function run(operation) {
    const rows = selected(), current = generation;
    if (state.running || !rows.length) return;
    Object.assign(state, {running: true, done: 0, total: rows.length, failed: 0}); onChange();
    for (const row of rows) {
      const ok = await operation(row);
      if (current !== generation) return;
      if (ok) state.keys.delete(cardKey(row.item)); else state.failed++;
      state.done++; onChange();
    }
    state.running = false; onChange();
  }
  const moveTo = groupId => run(async row => !await favorites.move(row.item, groupId));
  // Same rule as the star: a subscription must not outlive its favorite.
  const remove = () => run(async row => {
    if (subscriptions.find(row.item) && !await subscriptions.remove(row.item)) return false;
    await favorites.toggle(row.item);
    return !favorites.find(row.item);
  });
  return {state, selected, clear, setActive, toggle, setAll, prune, moveTo, remove};
}
