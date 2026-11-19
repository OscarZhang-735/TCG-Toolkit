import {apiRequest, getToken} from './api.js';

function browserStorage() {
  try { return globalThis.sessionStorage; } catch { return null; }
}

export function createStorefrontClient({shop, storageKey, rowField, credentialFields}, {storage = browserStorage(), request = apiRequest,
  getSessionToken = getToken} = {}) {
  const base = '/api/' + shop + '/cart';
  const valid = credentials => Object.entries(credentialFields).every(([key, pattern]) =>
    typeof credentials?.[key] === 'string' && pattern.test(credentials[key]));
  let userId = null, token = null, generation = 0, send = request;
  const erase = () => { try { storage?.removeItem(storageKey); } catch { /* nothing to clear */ } };
  function stored() {
    try {
      const value = JSON.parse(storage?.getItem(storageKey) ?? 'null');
      if (value && typeof value.userId === 'string' && valid(value.credentials)) return value;
    } catch { /* malformed or unavailable */ }
    erase();
    return null;
  }
  function requireAccount() {
    if (!userId) throw new Error('authentication_required');
    if (!token || getSessionToken() !== token) { deactivate(); throw new Error('authentication_required'); }
  }
  function deactivate({clear = true} = {}) {
    generation++; userId = null; token = null;
    if (clear) erase();
  }
  async function call(path, method, payload = {}, headers = {}) {
    requireAccount();
    const value = stored();
    if (value?.userId !== userId) { erase(); throw new Error(shop + '_credentials_missing'); }
    const started = generation;
    const response = await send(path, {method, cache: 'no-store', headers: {'Content-Type': 'application/json', ...headers},
      body: JSON.stringify({...payload, credentials: value.credentials})});
    if (started !== generation) throw new DOMException('Stale session', 'AbortError');
    if (response.status === 401) { deactivate(); throw new Error('authentication_required'); }
    const result = await response.json().catch(() => ({}));
    if (started !== generation) throw new DOMException('Stale session', 'AbortError');
    if (!response.ok) {
      const error = new Error(shop + '_request_failed');
      error.status = response.status; error.detail = result.detail;
      throw error;
    }
    return result;
  }
  return {
    activateUser(id, authenticatedRequest = request) {
      generation++;
      if (stored()?.userId !== id) erase();
      userId = id; token = getSessionToken(); send = authenticatedRequest;
    },
    deactivate,
    clearCredentials() { generation++; erase(); },
    hasCredentials() { requireAccount(); return stored()?.userId === userId; },
    saveCredentials(input) {
      requireAccount();
      if (!valid(input)) throw new Error('invalid_' + shop + '_credentials');
      const credentials = Object.fromEntries(Object.keys(credentialFields).map(key => [key, input[key]]));
      generation++;
      try {
        if (!storage) throw new Error();
        storage.setItem(storageKey, JSON.stringify({userId, credentials}));
      } catch { erase(); throw new Error('credential_storage_unavailable'); }
    },
    query() { return call(base + '/query', 'POST'); },
    add({product_url, counter = 1}, idempotencyKey) {
      if (typeof idempotencyKey !== 'string' || !/^[A-Za-z0-9_.:-]{1,128}$/.test(idempotencyKey))
        throw new Error('idempotency_key_required');
      return call(base, 'POST', {product_url, counter}, {'Idempotency-Key': idempotencyKey});
    },
    setQuantity(rowId, quantity) { return call(base + '/quantity', 'PUT', {[rowField]: rowId, quantity}); },
    remove(rowId) { return call(base, 'DELETE', {[rowField]: rowId}); },
  };
}
