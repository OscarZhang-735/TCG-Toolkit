import {apiRequest, getToken} from './api.js';

// Hobby Station sales-cart session cookies, kept like the YuYu-Tei ones: only in this tab's
// sessionStorage, bound to the signed-in account, and sent with each request.
export const HOBBYSTATION_CREDENTIALS_KEY = 'tcg.hobbystation.credentials';
const VALUE = /^[A-Za-z0-9%+/_=.,-]{1,512}$/;
function browserStorage() {
  try { return globalThis.sessionStorage; } catch { return null; }
}

// Accepts the bare value or a pasted "name=value" pair, as copied from DevTools.
export function cookieValue(input, name) {
  const value = String(input ?? '').trim().replace(new RegExp(`^${name}=`), '').replace(/;$/, '');
  return VALUE.test(value) ? value : null;
}

export function createHobbystationClient({storage = browserStorage(), request = apiRequest,
  getSessionToken = getToken} = {}) {
  let userId = null, token = null, generation = 0, send = request;
  const erase = () => { try { storage?.removeItem(HOBBYSTATION_CREDENTIALS_KEY); } catch { /* nothing to clear */ } };
  function stored() {
    try {
      const value = JSON.parse(storage?.getItem(HOBBYSTATION_CREDENTIALS_KEY) ?? 'null');
      if (value && typeof value.userId === 'string' && typeof value.credentials?.eccube === 'string'
          && typeof value.credentials?.eccube_bot_check === 'string') return value;
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
  async function call(path, method, payload = {}) {
    requireAccount();
    const value = stored();
    if (value?.userId !== userId) { erase(); throw new Error('hobbystation_credentials_missing'); }
    const started = generation;
    const response = await send(path, {method, cache: 'no-store', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...payload, credentials: value.credentials})});
    if (started !== generation) throw new DOMException('Stale session', 'AbortError');
    if (response.status === 401) { deactivate(); throw new Error('authentication_required'); }
    const result = await response.json().catch(() => ({}));
    if (started !== generation) throw new DOMException('Stale session', 'AbortError');
    if (!response.ok) {
      const error = new Error('hobbystation_request_failed');
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
    saveCredentials({eccube, eccube_bot_check}) {
      requireAccount();
      if (!VALUE.test(eccube ?? '') || !VALUE.test(eccube_bot_check ?? '')) throw new Error('invalid_hobbystation_credentials');
      generation++;
      try {
        if (!storage) throw new Error();
        storage.setItem(HOBBYSTATION_CREDENTIALS_KEY, JSON.stringify({userId, credentials: {eccube, eccube_bot_check}}));
      } catch { erase(); throw new Error('credential_storage_unavailable'); }
    },
    query() { return call('/api/hobbystation/cart/query', 'POST'); },
  };
}

export const hobbystationClient = createHobbystationClient();
