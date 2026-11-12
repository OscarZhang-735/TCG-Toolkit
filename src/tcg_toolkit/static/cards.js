// price is the editor's text ('' = not set); see payload() for what is sent.
export const blankCard = () => ({game: '', card_number: '', rarity: '', name: '', quantity: 1, price: '', currency: 'JPY', notes: '', image_url: ''});
// Currencies a unit price may be entered in (the same ones the sidebar can display).
export const CURRENCIES = ['JPY', 'CNY', 'CAD'];
// Yen value of a card's price, for comparing prices entered in different currencies.
// rates[code] is how much of that currency one yen buys; null when unpriced or the rate is unknown.
export function yenPrice(row, rates) {
  if (row.price == null) return null;
  const currency = row.currency ?? 'JPY';
  if (currency === 'JPY') return row.price;
  return rates[currency] ? row.price / rates[currency] : null;
}
// Price lookup (/api/prices). Merchants name games by lowercase id; library games use display
// names (WS EN, ChaosTCG), so map one to the other. Only YuYu-Tei exists for now.
export const PRICE_MERCHANT = 'yuyutei';
export function priceGame(game) {
  const id = (game ?? '').trim().toLowerCase().replace(/\s+/g, '_');
  return id === 'chaostcg' ? 'chaos' : id;
}
// Offers with the draft's rarity first, otherwise in the merchant's order.
export function rankOffers(items, rarity) {
  const wanted = (rarity ?? '').trim().toLowerCase();
  const match = item => Boolean(wanted) && (item.rarity ?? '').trim().toLowerCase() === wanted;
  return [...items].sort((a, b) => match(b) - match(a));
}
// The offer a bulk lookup uses for a card: same rarity (case-insensitive) and a currency the
// library supports; among several, the lowest price, preferring offers in stock. null when none.
export function pickOffer(items, rarity) {
  const wanted = (rarity ?? '').trim().toLowerCase();
  if (!wanted) return null;
  const matches = items.filter(item => (item.rarity ?? '').trim().toLowerCase() === wanted &&
    CURRENCIES.includes(item.currency) && Number.isFinite(item.price) && item.price >= 0);
  const inStock = matches.filter(item => item.stock_status === 'in_stock');
  return (inStock.length ? inStock : matches).reduce((best, item) => best && best.price <= item.price ? best : item, null);
}
const priceErrors = {price_source_timeout: 'price_lookup_timeout', price_source_unavailable: 'price_lookup_unavailable',
  price_source_unreadable: 'price_lookup_unreadable', unsupported_merchant_game: 'price_lookup_unsupported',
  unsupported_merchant: 'price_lookup_unsupported'};
// Merchants that can price cards: [{id, name, games: Set(['ws', ...])}].
export async function fetchPriceMerchants(api, signal) {
  const response = await api('/api/prices/merchants', {cache: 'no-store', signal});
  if (!response.ok) throw new Error('price merchants failed');
  const data = await response.json();
  return data.items.map(item => ({id: item.id, name: item.name, games: new Set(item.games)}));
}
// Resolves to the offers; failures throw with a translation key in error.key.
export async function lookupPrices(api, {game, card_number}, signal, merchant = PRICE_MERCHANT) {
  const query = new URLSearchParams({merchant, game: priceGame(game), card_number: card_number.trim()});
  const response = await api(`/api/prices?${query}`, {cache: 'no-store', signal});
  const data = await response.json().catch(() => null);
  if (!response.ok) throw Object.assign(new Error('price lookup failed'), {key: priceErrors[data?.detail] ?? 'price_lookup_failed'});
  return data.items;
}
// Request body for a draft: the price goes as a decimal string so 12.34 stays exact; blank clears it.
const payload = draft => ({...draft, price: String(draft.price ?? '').trim() || null});
const draftOf = row => Object.fromEntries(Object.keys(blankCard()).map(k => [k,
  k === 'price' ? (row.price == null ? '' : String(row.price)) : row[k] ?? blankCard()[k]]));
const key = value => value.trim().toLowerCase();
// min/max are inclusive bounds on priceOf(row) (null: no limit); a card whose price is null never meets a bound.
export function filterCards(rows, {query = '', game = '', rarity = '', min = null, max = null, priceOf = row => row.price} = {}) {
  return rows.filter(row => (!game || key(row.game) === key(game)) && (!rarity || key(row.rarity) === key(rarity)) &&
    (min === null || (priceOf(row) != null && priceOf(row) >= min)) && (max === null || (priceOf(row) != null && priceOf(row) <= max)) &&
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
// Server error details the group bar and dialogs translate; the favorites wording is generic.
const errorKeys = {card_group_name_taken: 'favorite_group_name_taken', card_group_not_found: 'favorite_group_missing',
  card_not_found: 'cards_missing'};
const stale = key => key === 'favorite_group_missing' || key === 'cards_missing';
export function createCards(onChange) {
  let request, generation = 0, controller = new AbortController();
  // group is the selected group id (null shows every card); draftGroup is the editor's group choice:
  // the selected tab for a new card, the card's own group when editing.
  const initial = () => ({records: [], groups: [], group: null, groupBusy: false, ready: false, busy: false, loading: false,
    error: '', duplicate: '', editing: '', draft: blankCard(), draftGroup: null});
  const state = initial();
  const visible = () => state.group === null ? state.records : state.records.filter(row => row.group_id === state.group);
  function clear() {
    generation++; controller.abort(); controller = new AbortController();
    Object.assign(state, initial());
    onChange();
  }
  async function call(path, method, body) {
    const response = await request('/api/cards' + path, {method, cache: 'no-store', signal: controller.signal,
      ...(method === 'GET' ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body ?? {})})});
    const data = response.status === 204 ? null : await response.json();
    if (!response.ok) throw Object.assign(new Error(), {key: errorKeys[data?.detail] || (response.status === 409 ? 'cards_duplicate' : response.status === 422 ? 'cards_invalid' : 'cards_failed'), duplicate: data?.detail?.id});
    return data;
  }
  async function load() {
    const current = generation;
    state.loading = true; state.error = ''; onChange();
    try {
      const [cards, groups] = await Promise.all([call('', 'GET'), call('/groups', 'GET')]);
      if (current !== generation) return;
      state.records = cards.items; state.groups = groups.items; state.ready = true;
      if (!state.groups.some(group => group.id === state.group)) state.group = null;
      if (!state.groups.some(group => group.id === state.draftGroup)) state.draftGroup = null;
    } catch (error) {if (current === generation && error.name !== 'AbortError') state.error = 'cards_failed';}
    finally {if (current === generation) {state.loading = false; onChange();}}
  }
  function edit(id = '') {
    if (state.busy) return;
    const row = state.records.find(row => row.id === id);
    if (id && !row) return;
    state.editing = id; state.draft = row ? draftOf(row) : blankCard();
    state.draftGroup = row ? row.group_id ?? null : state.group;
    state.error = ''; state.duplicate = ''; onChange();
  }
  async function mutate(removeId) {
    if (state.busy || !state.ready) return;
    const current = generation, id = removeId || state.editing;
    state.busy = true; state.error = ''; state.duplicate = ''; onChange();
    try {
      const body = removeId ? {} : {...payload(state.draft), group_id: state.draftGroup};
      const result = await call(id ? '/' + id : '', removeId ? 'DELETE' : id ? 'PUT' : 'POST', body);
      if (current !== generation) return;
      if (removeId) state.records = state.records.filter(row => row.id !== id);
      else if (id) state.records = state.records.map(row => row.id === id ? result : row);
      else state.records.unshift(result);
      if (!removeId || state.editing === removeId) {state.editing = ''; state.draft = blankCard(); state.draftGroup = state.group;}
    } catch (error) {
      if (current === generation && error.name !== 'AbortError') {
        state.error = error.key || 'cards_failed'; state.duplicate = error.duplicate || '';
        if (stale(error.key)) load();
      }
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
  // groupId null takes the card out of its group; resolves to an error key ('' on success).
  async function move(id, groupId) {
    const current = generation, row = state.records.find(card => card.id === id);
    if (!state.ready || state.busy || !row) return 'cards_failed';
    if (row.group_id === groupId) return '';
    state.busy = true; onChange();
    try {
      const result = await call(`/${id}/group`, 'PUT', {group_id: groupId});
      if (current === generation) state.records = state.records.map(card => card.id === id ? result : card);
      return '';
    } catch (error) {
      return failure(error, current);
    } finally {
      if (current === generation) {state.busy = false; onChange();}
    }
  }
  // Group writes report a translation key ('' on success) so the open dialog can show it.
  async function editGroup(operation) {
    const current = generation;
    if (!state.ready || state.groupBusy) return 'cards_failed';
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
  // Another tab may have deleted the group or card; reload so the page matches the server again.
  function failure(error, current) {
    if (current !== generation || error.name === 'AbortError') return '';
    if (stale(error.key)) load();
    return error.key || 'cards_failed';
  }
  // Sets one card's unit price, keeping its other fields and group; resolves to an error key ('' on success).
  async function setPrice(id, price, currency) {
    const current = generation, row = state.records.find(card => card.id === id);
    if (!state.ready || state.busy || !row) return 'cards_failed';
    state.busy = true; onChange();
    try {
      const result = await call('/' + id, 'PUT', {...payload(draftOf(row)), price: Number(price).toFixed(2), currency});
      if (current === generation) state.records = state.records.map(card => card.id === id ? result : card);
      return '';
    } catch (error) {
      return failure(error, current);
    } finally {
      if (current === generation) {state.busy = false; onChange();}
    }
  }
  const createGroup = name => editGroup(async current => {
    const group = await call('/groups', 'POST', {name});
    if (current === generation) {state.groups.push(group); state.group = group.id;}
  });
  const renameGroup = (id, name) => editGroup(async current => {
    const group = await call(`/groups/${id}`, 'PUT', {name});
    if (current === generation) state.groups = state.groups.map(row => row.id === id ? group : row);
  });
  // Deleting a group keeps its cards; the server leaves them ungrouped.
  const deleteGroup = id => editGroup(async current => {
    await call(`/groups/${id}`, 'DELETE');
    if (current !== generation) return;
    state.groups = state.groups.filter(group => group.id !== id);
    state.records = state.records.map(row => row.group_id === id ? {...row, group_id: null} : row);
    if (state.group === id) state.group = null;
    if (state.draftGroup === id) state.draftGroup = null;
  });
  function select(id) {
    if (state.busy || (id !== null && !state.groups.some(group => group.id === id))) return;
    state.group = id; onChange();
  }
  return {state, visible, clear, load, edit, importFile, save: () => mutate(), remove: id => mutate(id),
    move, setPrice, select, createGroup, renameGroup, deleteGroup, activate(api) {clear(); request = api; return load();}};
}
