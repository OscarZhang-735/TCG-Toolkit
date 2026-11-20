import {cardKey} from './favorites.js';

// Bulk actions run one card at a time through the same models as the card controls.
export function createSelection(favorites, subscriptions, onChange, pause = () => new Promise(resolve => setTimeout(resolve, 1000))) {
  let generation = 0;
  const initial = () => ({active: false, keys: new Set(), running: false, action: '', done: 0, total: 0, failed: 0});
  const state = initial();
  const selected = () => favorites.state.records.filter(row => state.keys.has(cardKey(row.item)));
  function clear() {
    generation++;
    Object.assign(state, initial());
    onChange();
  }
  function setActive(active) {
    if (state.running) return;
    Object.assign(state, {active, keys: new Set(), action: '', failed: 0}); onChange();
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
  // Moves and removals deselect finished cards; with keep (subscribe, refresh) the whole
  // selection stays so another action can follow. gap spaces out calls to the card source.
  async function run(operation, {keep = false, gap = false, items = selected().map(row => row.item), action = ''} = {}) {
    const current = generation;
    if (!state.active || state.running || !items.length) return;
    Object.assign(state, {running: true, action, done: 0, total: items.length, failed: 0}); onChange();
    for (const [index, item] of items.entries()) {
      if (gap && index) { await pause(); if (current !== generation) return; }
      const ok = await operation(item);
      if (current !== generation) return;
      if (!ok) state.failed++; else if (!keep) state.keys.delete(cardKey(item));
      state.done++; onChange();
    }
    state.running = false; onChange();
  }
  const moveTo = groupId => run(async item => !await favorites.move(item, groupId));
  const subscribe = () => run(item => subscriptions.add(item), {keep: true});
  const unsubscribe = () => run(item => subscriptions.remove(item), {keep: true});
  // Refresh queries the source for each card, so it keeps the same pace as "Refresh all".
  const refresh = () => run(async item => {
    await favorites.refresh(item);
    return favorites.state.messages.get(cardKey(item)) === 'favorite_updated';
  }, {keep: true, gap: true});
  const remove = () => run(async item => {
    await favorites.toggle(item);
    return !favorites.find(item);
  });
  // This action targets the current filtered list, including unchecked cards.
  const maximize = (matches, add) => run(add, {items: matches, keep: true, action: 'maximize'});
  return {state, selected, clear, setActive, toggle, setAll, prune, moveTo, remove, subscribe, unsubscribe, refresh, maximize};
}
