// The server stores normalized URLs (no trailing slash); match favorites the same way.
export const subscriptionKey = item => JSON.stringify([item.game, item.product_url.replace(/\/+$/, '')]);
const errorKeys = {verified_email_required: 'subscription_email_required', product_timeout: 'subscription_timeout',
  product_unavailable: 'subscription_unreadable', product_unreadable: 'subscription_unreadable'};

// Card-update subscriptions; independent of favorites, though only the favorites page shows them.
export function createSubscriptions(onChange) {
  let generation = 0, request, controller = new AbortController();
  const state = {records: [], busy: new Set(), messages: new Map(), ready: false, error: ''};
  const find = item => state.records.find(row => subscriptionKey(row.item) === subscriptionKey(item));
  function clear() {
    generation++; controller.abort(); controller = new AbortController();
    Object.assign(state, {records: [], busy: new Set(), messages: new Map(), ready: false, error: ''});
    onChange();
  }
  async function call(url, method, body) {
    const response = await request(url, {method, signal: controller.signal, cache: 'no-store',
      ...(method === 'GET' ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body ?? {})})});
    const data = response.status === 204 ? null : await response.json();
    if (!response.ok) {
      const error = new Error();
      error.detail = data?.detail;
      error.key = response.status === 422 ? 'subscription_unsupported'
        : errorKeys[data?.detail] || (response.status === 504 ? 'subscription_timeout' : 'subscription_failed');
      throw error;
    }
    return data;
  }
  async function load() {
    const current = generation;
    state.error = ''; onChange();
    try {
      const data = await call('/api/subscriptions', 'GET');
      if (current !== generation) return;
      state.records = data.items; state.ready = true;
    } catch (error) {if (current === generation && error.name !== 'AbortError') state.error = 'subscription_load_failed';}
    if (current === generation) onChange();
  }
  // Resolves true when the card ends up in the requested state.
  async function operate(item, subscribe) {
    const key = subscriptionKey(item), current = generation;
    if (!state.ready || state.busy.has(key)) return false;
    const saved = find(item);
    if (Boolean(saved) === subscribe) return true;
    state.busy.add(key); state.messages.delete(key); onChange();
    try {
      const result = await call(saved ? `/api/subscriptions/${saved.id}` : '/api/subscriptions',
        saved ? 'DELETE' : 'POST', saved ? {} : {game: item.game, product_url: item.product_url});
      if (current !== generation) return false;
      if (saved) state.records = state.records.filter(row => row.id !== saved.id);
      else state.records = [result, ...state.records.filter(row => row.id !== result.id)];
      return true;
    } catch (error) {
      if (current !== generation || error.name === 'AbortError') return false;
      // Already removed elsewhere (another tab): the goal is reached.
      if (saved && error.detail === 'subscription_not_found') {
        state.records = state.records.filter(row => row.id !== saved.id); return true;
      }
      state.messages.set(key, error.key);
      return false;
    } finally {
      if (current === generation) {state.busy.delete(key); onChange();}
    }
  }
  return {state, find, clear, load, toggle: item => operate(item, !find(item)), remove: item => operate(item, false),
    activate(api) {clear(); request = api; return load();}};
}
