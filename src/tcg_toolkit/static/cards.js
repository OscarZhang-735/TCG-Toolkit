export const blankCard = () => ({game: '', card_number: '', rarity: '', name: '', quantity: 1, notes: '', image_url: ''});
const key = value => value.trim().toLowerCase();
export function filterCards(rows, {query = '', game = '', rarity = ''} = {}) {
  return rows.filter(row => (!game || key(row.game) === key(game)) && (!rarity || key(row.rarity) === key(rarity)) &&
    (!query.trim() || key(row.name).includes(key(query)) || key(row.card_number).includes(key(query))));
}
export function summarizeCards(rows) {
  const result = {kinds: 0, quantity: 0, games: [], rarities: []};
  const games = new Map(), rarities = new Map();
  for (const row of rows) {
    const kinds = row.quantity > 0 ? 1 : 0;
    result.kinds += kinds; result.quantity += row.quantity;
    for (const [groups, label] of [[games, row.game], [rarities, row.rarity]]) {
      const group = groups.get(key(label)) || {label, kinds: 0, quantity: 0};
      group.kinds += kinds; group.quantity += row.quantity; groups.set(key(label), group);
    }
  }
  result.games = [...games.values()]; result.rarities = [...rarities.values()];
  return result;
}
export function createCards(onChange) {
  let request, generation = 0, controller = new AbortController();
  const state = {records: [], ready: false, busy: false, loading: false, error: '', duplicate: '', editing: '', draft: blankCard()};
  function clear() {
    generation++; controller.abort(); controller = new AbortController();
    Object.assign(state, {records: [], ready: false, busy: false, loading: false, error: '', duplicate: '', editing: '', draft: blankCard()});
    onChange();
  }
  async function call(path, method, body) {
    const response = await request('/api/cards' + path, {method, cache: 'no-store', signal: controller.signal,
      ...(method === 'GET' ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body ?? {})})});
    const data = response.status === 204 ? null : await response.json();
    if (!response.ok) throw Object.assign(new Error(), {key: response.status === 409 ? 'cards_duplicate' : response.status === 422 ? 'cards_invalid' : 'cards_failed', duplicate: data?.detail?.id});
    return data;
  }
  async function load() {
    const current = generation;
    state.loading = true; state.error = ''; onChange();
    try {
      const data = await call('', 'GET');
      if (current !== generation) return;
      state.records = data.items; state.ready = true;
    } catch (error) {if (current === generation && error.name !== 'AbortError') state.error = 'cards_failed';}
    finally {if (current === generation) {state.loading = false; onChange();}}
  }
  function edit(id = '') {
    if (state.busy) return;
    const row = state.records.find(row => row.id === id);
    if (id && !row) return;
    state.editing = id; state.draft = row ? Object.fromEntries(Object.keys(blankCard()).map(k => [k, row[k] ?? blankCard()[k]])) : blankCard();
    state.error = ''; state.duplicate = ''; onChange();
  }
  async function mutate(removeId) {
    if (state.busy || !state.ready) return;
    const current = generation, id = removeId || state.editing;
    state.busy = true; state.error = ''; state.duplicate = ''; onChange();
    try {
      const result = await call(id ? '/' + id : '', removeId ? 'DELETE' : id ? 'PUT' : 'POST', removeId ? {} : state.draft);
      if (current !== generation) return;
      if (removeId) state.records = state.records.filter(row => row.id !== id);
      else if (id) state.records = state.records.map(row => row.id === id ? result : row);
      else state.records.unshift(result);
      if (!removeId || state.editing === removeId) {state.editing = ''; state.draft = blankCard();}
    } catch (error) {
      if (current === generation && error.name !== 'AbortError') {state.error = error.key || 'cards_failed'; state.duplicate = error.duplicate || '';}
    } finally {if (current === generation) {state.busy = false; onChange();}}
  }
  async function importFile(file, format) {
    if (state.busy || state.loading || !state.ready) return;
    const current = generation;
    state.busy = true; onChange();
    try {
      const type = format === 'json' ? 'application/json' : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
      const response = await request(`/api/cards/import?format=${format}`, {method: 'POST', cache: 'no-store',
        headers: {'Content-Type': type}, body: file, signal: controller.signal});
      const result = await response.json();
      if (current !== generation) return;
      if (!response.ok) return {error: result.detail};
      await load();
      if (current === generation) return result;
    } catch {
      if (current === generation) return {error: 'cards_import_failed'};
    } finally {
      if (current === generation) {state.busy = false; onChange();}
    }
  }
  return {state, clear, load, edit, importFile, save: () => mutate(), remove: id => mutate(id), activate(api) {clear(); request = api; return load();}};
}
