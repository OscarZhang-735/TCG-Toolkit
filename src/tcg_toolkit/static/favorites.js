export const cardKey = item => JSON.stringify([item.game, item.product_url]);
const errorKeys = {refresh_product_missing: 'favorite_missing', refresh_parse_error: 'favorite_parse',
  favorite_group_name_taken: 'favorite_group_name_taken', favorite_group_not_found: 'favorite_group_missing'};

// One state owner for search buttons and the favorites page.
export function createFavorites(onChange, pause = signal => new Promise(resolve => {
  const done = () => {clearTimeout(timer); signal.removeEventListener('abort', done); resolve();};
  const timer = setTimeout(done, 1000);
  signal.addEventListener('abort', done, {once: true});
  if (signal.aborted) done();
})) {
  let generation = 0, request, controller = new AbortController();
  // group is the selected group id on the favorites page; null shows every group.
  const initial = () => ({records: [], groups: [], group: null, groupBusy: false, busy: new Set(), messages: new Map(),
    ready: false, error: '', running: false, done: 0, total: 0});
  const state = initial();
  const find = item => state.records.find(row => cardKey(row.item) === cardKey(item));
  const visible = () => state.group === null ? state.records : state.records.filter(row => row.group_id === state.group);
  function clear() {
    generation++; controller.abort(); controller = new AbortController();
    Object.assign(state, initial());
    onChange();
  }
  async function call(url, method, body) {
    const response = await request(url, {method, signal: controller.signal, cache: 'no-store',
      ...(method === 'GET' ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body ?? {})})});
    const data = response.status === 204 ? null : await response.json();
    if (!response.ok) {
      const error = new Error();
      error.key = response.status === 504 ? 'favorite_timeout' : errorKeys[data?.detail] || 'favorite_failed';
      throw error;
    }
    return data;
  }
  async function load() {
    const current = generation;
    state.error = ''; onChange();
    try {
      const [favorites, groups] = await Promise.all([call('/api/favorites', 'GET'), call('/api/favorites/groups', 'GET')]);
      if (current !== generation) return;
      state.records = favorites.items; state.groups = groups.items; state.ready = true;
      if (!state.groups.some(group => group.id === state.group)) state.group = null;
    } catch (error) {if (current === generation && error.name !== 'AbortError') state.error = 'favorite_failed';}
    if (current === generation) onChange();
  }
  // groupId only applies when adding: the new favorite goes straight into that group.
  async function operate(item, refresh = false, groupId = null) {
    const key = cardKey(item), current = generation;
    if (!state.ready || state.busy.has(key)) return;
    const saved = find(item);
    if (refresh && !saved) return;
    state.busy.add(key); state.messages.delete(key); onChange();
    let removedItem;
    try {
      const result = await call(saved ? `/api/favorites/${saved.id}${refresh ? '/refresh' : ''}` : '/api/favorites',
        saved && !refresh ? 'DELETE' : 'POST', saved ? {} : groupId ? {...item, group_id: groupId} : item);
      if (current !== generation) return;
      if (!saved) state.records.unshift(result);
      else if (refresh) {state.records = state.records.map(row => row.id === saved.id ? result : row); state.messages.set(key, 'favorite_updated');}
      else {
        state.records = state.records.filter(row => row.id !== saved.id);
        removedItem = saved.item;
      }
    } catch (error) {
      if (current === generation && error.name !== 'AbortError') {
        state.messages.set(key, error.key || 'favorite_failed');
        if (error.key === 'favorite_group_missing') load();
      }
    } finally {
      if (current === generation) {state.busy.delete(key); onChange(removedItem);}
    }
  }
  // Refreshes what the favorites page shows: the selected group, or everything.
  async function refreshAll() {
    if (state.running || state.busy.size || !state.ready) return;
    const current = generation, signal = controller.signal, rows = [...visible()];
    state.running = true; state.done = 0; state.total = rows.length; onChange();
    for (const row of rows) {
      if (current !== generation) return;
      await operate(row.item, true);
      if (current !== generation) return;
      state.done++; onChange();
      if (state.done < rows.length) await pause(signal);
    }
    if (current === generation) {state.running = false; onChange();}
  }
  // groupId null takes the card out of its group.
  async function move(item, groupId) {
    const key = cardKey(item), current = generation, saved = find(item);
    if (!state.ready || !saved || state.busy.has(key)) return 'favorite_failed';
    if (saved.group_id === groupId) return '';
    state.busy.add(key); state.messages.delete(key); onChange();
    try {
      const result = await call(`/api/favorites/${saved.id}/group`, 'PUT', {group_id: groupId});
      if (current === generation) state.records = state.records.map(row => row.id === saved.id ? result : row);
      return '';
    } catch (error) {
      return failure(error, current);
    } finally {
      if (current === generation) {state.busy.delete(key); onChange();}
    }
  }
  // Group writes report a translation key ('' on success) so the open dialog can show it.
  async function editGroup(operation) {
    const current = generation;
    if (!state.ready || state.groupBusy) return 'favorite_failed';
    state.groupBusy = true; onChange();
    try {
      await operation(current);
      return '';
    } catch (error) {
      return failure(error, current);
    } finally {
      if (current === generation) {state.groupBusy = false; onChange();}
    }
  }
  // Another tab may have deleted the group; reload so the page matches the server again.
  function failure(error, current) {
    if (current !== generation || error.name === 'AbortError') return '';
    if (error.key === 'favorite_group_missing') load();
    return error.key || 'favorite_failed';
  }
  const createGroup = name => editGroup(async current => {
    const group = await call('/api/favorites/groups', 'POST', {name});
    if (current === generation) {state.groups.push(group); state.group = group.id;}
  });
  const renameGroup = (id, name) => editGroup(async current => {
    const group = await call(`/api/favorites/groups/${id}`, 'PUT', {name});
    if (current === generation) state.groups = state.groups.map(row => row.id === id ? group : row);
  });
  // Deleting a group keeps its cards; the server leaves them ungrouped.
  const deleteGroup = id => editGroup(async current => {
    await call(`/api/favorites/groups/${id}`, 'DELETE');
    if (current !== generation) return;
    state.groups = state.groups.filter(group => group.id !== id);
    state.records = state.records.map(row => row.group_id === id ? {...row, group_id: null} : row);
    if (state.group === id) state.group = null;
  });
  function select(id) {
    if (state.running || (id !== null && !state.groups.some(group => group.id === id))) return;
    state.group = id; onChange();
  }
  return {state, find, visible, clear, load, toggle: (item, groupId = null) => operate(item, false, groupId), refresh: item => operate(item, true), refreshAll,
    move, select, createGroup, renameGroup, deleteGroup,
    activate(api) {clear(); request = api; return load();}};
}
