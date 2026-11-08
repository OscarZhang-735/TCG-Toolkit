import {createMessages, messageTier} from './notices.js';
// The server stores normalized URLs (no trailing slash); match favorites the same way.
export const subscriptionKey = item => JSON.stringify([item.game, item.product_url.replace(/\/+$/, '')]);
const errorKeys = {verified_email_required: 'subscription_email_required', product_timeout: 'subscription_timeout',
  product_unavailable: 'subscription_unreadable', product_unreadable: 'subscription_unreadable'};

// Card-update subscriptions; independent of favorites, though only the favorites page shows them.
export function createSubscriptions(onChange) {
  let generation = 0, request, controller = new AbortController();
  let pendingLoad = null;
  const messages = createMessages(onChange);
  // notice: an account-level problem (no verified email) shown once for the page.
  const state = {records: [], busy: new Set(), messages: messages.map, ready: false, error: '', notice: ''};
  const find = item => state.records.find(row => subscriptionKey(row.item) === subscriptionKey(item));
  function clear() {
    generation++; controller.abort(); controller = new AbortController();
    pendingLoad = null; messages.clear();
    Object.assign(state, {records: [], busy: new Set(), ready: false, error: '', notice: ''});
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
    const forgotten = new Set();
    pendingLoad = forgotten;
    state.error = ''; onChange();
    try {
      const data = await call('/api/subscriptions', 'GET');
      if (pendingLoad !== forgotten) return;
      state.records = data.items.filter(row => !forgotten.has(subscriptionKey(row.item)));
      state.ready = true;
    } catch (error) {
      if (pendingLoad === forgotten && error.name !== 'AbortError') state.error = 'subscription_load_failed';
    } finally {
      if (pendingLoad === forgotten) {pendingLoad = null; onChange();}
    }
  }
  // A successful favorite deletion also removed the subscription on the server.
  function forget(item) {
    const key = subscriptionKey(item);
    state.records = state.records.filter(row => subscriptionKey(row.item) !== key);
    messages.remove(key);
    pendingLoad?.add(key);
  }
  // Resolves true when the card ends up in the requested state.
  async function operate(item, subscribe) {
    const key = subscriptionKey(item), current = generation;
    if (!state.ready || state.busy.has(key)) return false;
    const saved = find(item);
    if (Boolean(saved) === subscribe) return true;
    state.busy.add(key); messages.remove(key); onChange();
    try {
      const result = await call(saved ? `/api/subscriptions/${saved.id}` : '/api/subscriptions',
        saved ? 'DELETE' : 'POST', saved ? {} : {game: item.game, product_url: item.product_url});
      if (current !== generation) return false;
      state.notice = '';
      if (saved) state.records = state.records.filter(row => row.id !== saved.id);
      else state.records = [result, ...state.records.filter(row => row.id !== result.id)];
      return true;
    } catch (error) {
      if (current !== generation || error.name === 'AbortError') return false;
      // Already removed elsewhere (another tab): the goal is reached.
      if (saved && error.detail === 'subscription_not_found') {
        state.records = state.records.filter(row => row.id !== saved.id); return true;
      }
      if (messageTier(error.key) === 'account') state.notice = error.key;
      else messages.set(key, error.key);
      return false;
    } finally {
      if (current === generation) {state.busy.delete(key); onChange();}
    }
  }
  return {state, bubbleStarted: item => messages.started(subscriptionKey(item)), dismissNotice() { state.notice = ''; onChange(); },
    find, clear, load, forget, toggle: item => operate(item, !find(item)), add: item => operate(item, true), remove: item => operate(item, false),
    activate(api) {clear(); request = api; return load();}};
}
