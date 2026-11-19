import {apiRequest, getToken} from '../api.js';

export const CREDENTIALS_KEY = 'tcg.yuyutei.credentials';
function browserStorage() {
  try { return globalThis.sessionStorage; } catch { return null; }
}

// No UI: callers activate the authenticated account and supply its request boundary.
export function createYuyuteiClient({storage = browserStorage(), request = apiRequest,
  getSessionToken = getToken} = {}) {
  let userId = null, token = null, generation = 0, send = request;
  const pending = new Set();
  function invalidate() {
    generation++;
    for (const controller of pending) controller.abort();
    pending.clear();
  }
  function erase() {
    try { storage?.removeItem(CREDENTIALS_KEY); } catch { /* no persistent fallback */ }
  }
  function stored() {
    try {
      const value = JSON.parse(storage?.getItem(CREDENTIALS_KEY) ?? 'null');
      if (value && typeof value.userId === 'string' &&
          typeof value.credentials?.cookie === 'string' &&
          typeof value.credentials?.csrf_token === 'string') return value;
    } catch { /* malformed/unavailable storage cannot supply credentials */ }
    erase();
    return null;
  }
  function deactivate({clear = true} = {}) {
    invalidate(); userId = null; token = null;
    if (clear) erase();
  }
  function requireAccount() {
    // Not activated yet (e.g. during a reload, before auth.js restores the account):
    // refuse, but keep this tab's credentials for activateUser to pick up.
    if (!userId) throw new Error('authentication_required');
    if (!token || getSessionToken() !== token) {
      deactivate();
      throw new Error('authentication_required');
    }
  }
  function assertCurrent(started) {
    if (started !== generation) throw new DOMException('Stale session', 'AbortError');
    requireAccount();
  }
  async function call(path, method, payload = {}, headers = {}) {
    requireAccount();
    const value = stored();
    if (!value || value.userId !== userId) {
      erase(); throw new Error('yuyutei_credentials_missing');
    }
    const started = generation, controller = new AbortController();
    pending.add(controller);
    try {
      const response = await send(path, {method, cache: 'no-store', signal: controller.signal,
        headers: {'Content-Type': 'application/json', ...headers},
        body: JSON.stringify({...payload, credentials: {
          cookie: value.credentials.cookie, csrf_token: value.credentials.csrf_token}})});
      assertCurrent(started);
      if (response.status === 401) {
        deactivate(); throw new Error('authentication_required');
      }
      const result = await response.json();
      assertCurrent(started);
      if (!response.ok) {
        const error = new Error('cart_request_failed');
        error.status = response.status; error.detail = result.detail;
        throw error;
      }
      return result;
    } finally { pending.delete(controller); }
  }
  return {
    activateUser(id, authenticatedRequest = request) {
      invalidate();
      const previous = stored();
      if (previous && previous.userId !== id) erase();
      userId = id; token = getSessionToken(); send = authenticatedRequest;
    },
    deactivate,
    clearCredentials() { invalidate(); erase(); },
    hasCredentials() {
      requireAccount();
      const value = stored();
      return value?.userId === userId;
    },
    saveCredentials({cookie, csrf_token}) {
      requireAccount();
      if (typeof cookie !== 'string' || !cookie.trim() || typeof csrf_token !== 'string' || !csrf_token.trim())
        throw new Error('invalid_yuyutei_credentials');
      invalidate();
      try {
        if (!storage) throw new Error();
        storage.setItem(CREDENTIALS_KEY, JSON.stringify({userId, credentials: {cookie, csrf_token}}));
      } catch { erase(); throw new Error('credential_storage_unavailable'); }
    },
    query() { return call('/api/yuyutei/cart/query', 'POST'); },
    add(card, idempotencyKey) {
      if (typeof idempotencyKey !== 'string' || !/^[A-Za-z0-9_.:-]{1,128}$/.test(idempotencyKey))
        throw new Error('idempotency_key_required');
      return call('/api/yuyutei/cart', 'POST', card, {'Idempotency-Key': idempotencyKey});
    },
    remove(card) { return call('/api/yuyutei/cart', 'DELETE', card); },
    // Lowers one cart row to an absolute target; repeating it cannot lower twice.
    setQuantity(card, quantity) { return call('/api/yuyutei/cart/quantity', 'PUT', {...card, quantity}); }
  };
}

export const yuyuteiClient = createYuyuteiClient();
