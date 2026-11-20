import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const base = new URL('../src/tcg_toolkit/static/', import.meta.url);
const moduleURL = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const parserURL = moduleURL(await readFile(new URL('cart/curl-credentials.js', base), 'utf8'));
const {parseCurlCredentials} = await import(parserURL);
const good = "curl 'https://yuyu-tei.jp/api/cart_service' -b 'other=discard; laravel_session=session%3D' -H 'X-CSRF-TOKEN: token-123'";
const expected = {cookie: 'session%3D', csrf_token: 'token-123'};
test('bash headers, cookie flags, continuations, quoting and case', () => {
  assert.deepEqual(parseCurlCredentials(good), expected);
  assert.deepEqual(parseCurlCredentials("curl https://yuyu-tei.jp \\\n --header 'Cookie: laravel_session=session%3D' \\\n --header='x-CsRf-ToKeN: token-123' --data-raw '{\"mode\":\"add\"}'"), expected);
  assert.deepEqual(parseCurlCredentials('curl.exe "https://yuyu-tei.jp" --cookie="laravel_session=session%3D" -H"X-CSRF-TOKEN: token-123"'), expected);
});
test('cmd caret escaped quotes and CRLF continuation', () => {
  assert.deepEqual(parseCurlCredentials('curl ^"https://yuyu-tei.jp^" ^\r\n -b ^"laravel_session=session%3D^" ^\r\n -H ^"X-CSRF-TOKEN: token-123^"'), expected);
});
test('invalid, ambiguous and unsupported inputs fail without reflecting secrets', () => {
  for (const input of ['', 'wget abc', good + " -H 'X-CSRF-TOKEN: second'", good + " -b 'laravel_session=second'",
    good.replace('other=discard', 'laravel_session=second'), good.replace('X-CSRF-TOKEN', 'X-XSRF-TOKEN'),
    good.replace('laravel_session=', 'XSRF-TOKEN='), good + " 'unterminated", good + '; echo secret',
    good.replace('token-123', '$(alert(1))'), good.replace('token-123', 'bad\r\ninjected'), 'x'.repeat(65537)]) {
    assert.throws(() => parseCurlCredentials(input), /^Error: cart_curl_invalid$/);
  }
});
const settingsSource = (await readFile(new URL('cart/cart-settings.js', base), 'utf8'))
  .replace("import {t, getLanguage} from '../i18n.js';", "const t = key => key, getLanguage = () => 'en';")
  .replace("'./curl-credentials.js'", JSON.stringify(parserURL));
const {bindCartSettings} = await import(moduleURL(settingsSource));
const clientSource = (await readFile(new URL('cart/yuyutei-client.js', base), 'utf8'))
  .replace("import {apiRequest, getToken} from '../api.js';", "const apiRequest = () => {throw Error('unexpected network')}, getToken = () => 'login';");
const {createYuyuteiClient, CREDENTIALS_KEY} = await import(moduleURL(clientSource));
function setup() {
  const elements = new Map();
  globalThis.document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, {value: '', textContent: '', disabled: false, classList: {toggle() {}}, setAttribute() {},
      handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; }});
    return elements.get(id);
  }};
  const storage = new Map();
  const client = createYuyuteiClient({storage: {getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k)}, getSessionToken: () => 'login'});
  client.activateUser('alice');
  const cart = {state: {configured: false, loading: false}, refreshes: 0, refresh() { this.refreshes++; this.state.loading = true; }};
  const settings = bindCartSettings(client, cart);
  const el = id => document.getElementById(id);
  return {storage, client, cart, settings, el, save: () => el('cart-curl-save').handlers.click()};
}
test('import saves only extracted credentials in account-scoped session storage, then verifies once', () => {
  const {storage, cart, settings, el, save} = setup();
  el('cart-curl').value = good; el('cart-cookie').value = 'old';
  save();
  assert.deepEqual(JSON.parse(storage.get(CREDENTIALS_KEY)), {userId: 'alice', credentials: expected});
  assert.equal(el('cart-curl').value, ''); assert.equal(el('cart-cookie').value, '');
  // Same as the Cookie + CSRF route: one cart read verifies the saved values.
  assert.equal(el('cart-settings-status').textContent, 'cart_verifying');
  assert.equal(cart.refreshes, 1);
  el('cart-curl').value = good; settings.reset(); assert.equal(el('cart-curl').value, '');
});
test('invalid import preserves previous credentials and draft; account change clears stored credentials', () => {
  const {storage, client, el, save, settings} = setup();
  client.saveCredentials(expected); const previous = storage.get(CREDENTIALS_KEY);
  el('cart-curl').value = 'broken'; save();
  assert.equal(storage.get(CREDENTIALS_KEY), previous); assert.equal(el('cart-curl').value, 'broken');
  assert.equal(el('cart-settings-status').textContent, 'cart_curl_invalid');
  client.activateUser('bob'); settings.reset();
  assert.equal(storage.has(CREDENTIALS_KEY), false); assert.equal(el('cart-curl').value, '');
});
test('storage failure preserves draft and does not claim success', () => {
  const {client, el, save} = setup();
  client.saveCredentials = () => { throw Error('credential_storage_unavailable'); };
  el('cart-curl').value = good; save();
  assert.equal(el('cart-curl').value, good);
  assert.equal(el('cart-settings-status').textContent, 'cart_storage_unavailable');
});
