import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const base = new URL('../src/tcg_toolkit/static/', import.meta.url);
const moduleURL = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const sharedClient = moduleURL((await readFile(new URL('storefront-client.js', base), 'utf8'))
  .replace("import {apiRequest, getToken} from './api.js';", 'const apiRequest = null, getToken = () => null;'));
const clientURL = moduleURL((await readFile(new URL('hobbystation-client.js', base), 'utf8'))
  .replace("'./storefront-client.js'", JSON.stringify(sharedClient)));
const sharedSettings = moduleURL((await readFile(new URL('storefront-settings.js', base), 'utf8'))
  .replace("import {t, getLanguage} from './i18n.js';", "const t = key => key, getLanguage = () => 'en';"));
const {createHobbystationClient, cookieValue, HOBBYSTATION_CREDENTIALS_KEY} = await import(clientURL);
const {hobbystationErrorKey, bindHobbystationSettings} = await import(moduleURL(
  (await readFile(new URL('hs-cart-settings.js', base), 'utf8'))
    .replace("'./storefront-settings.js'", JSON.stringify(sharedSettings))
    .replace("'./hobbystation-client.js'", JSON.stringify(clientURL))));

function memoryStorage() {
  const values = new Map();
  return {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key), values};
}
const response = (status, body) => ({ok: status < 400, status, json: async () => body});
const tick = () => new Promise(resolve => setTimeout(resolve));

test('cookieValue accepts bare values and pasted name=value pairs', () => {
  assert.equal(cookieValue(' abc123 ', 'eccube'), 'abc123');
  assert.equal(cookieValue('eccube=abc123;', 'eccube'), 'abc123');
  assert.equal(cookieValue('eccube_bot_check=v3rified_x', 'eccube_bot_check'), 'v3rified_x');
  assert.equal(cookieValue('a b', 'eccube'), null);
  assert.equal(cookieValue('', 'eccube'), null);
  assert.equal(cookieValue('x'.repeat(513), 'eccube'), null);
});

test('query sends both cookies under credentials, scoped to the signed-in account', async () => {
  const storage = memoryStorage(), calls = [];
  const client = createHobbystationClient({storage, getSessionToken: () => 'tok',
    request: async (path, options) => { calls.push([path, options]); return response(200, {total_quantity: 2}); }});
  client.activateUser('u1');
  assert.equal(client.hasCredentials(), false);
  await assert.rejects(client.query(), {message: 'hobbystation_credentials_missing'});
  client.saveCredentials({eccube: 'sess', eccube_bot_check: 'bot'});
  assert.equal(client.hasCredentials(), true);
  assert.deepEqual(await client.query(), {total_quantity: 2});
  const [path, options] = calls[0];
  assert.equal(path, '/api/hobbystation/cart/query');
  assert.equal(options.method, 'POST');
  assert.deepEqual(JSON.parse(options.body), {credentials: {eccube: 'sess', eccube_bot_check: 'bot'}});
  // Another account in the same tab never sees them.
  client.activateUser('u2');
  assert.equal(client.hasCredentials(), false);
  assert.equal(storage.values.has(HOBBYSTATION_CREDENTIALS_KEY), false);
});

test('saving requires both values and errors carry the server detail', async () => {
  const client = createHobbystationClient({storage: memoryStorage(), getSessionToken: () => 'tok',
    request: async () => response(409, {detail: {type: 'hobbystation_credentials_rejected'}})});
  client.activateUser('u1');
  assert.throws(() => client.saveCredentials({eccube: 'sess'}), {message: 'invalid_hobbystation_credentials'});
  client.saveCredentials({eccube: 'sess', eccube_bot_check: 'bot'});
  const error = await client.query().catch(e => e);
  assert.equal(error.status, 409);
  assert.equal(hobbystationErrorKey(error), 'hs_cart_credentials_rejected');
  assert.equal(hobbystationErrorKey({detail: 'invalid_hobbystation_request'}), 'hs_cart_invalid_input');
  assert.equal(hobbystationErrorKey({detail: {type: 'hobbystation_unavailable'}}), 'hs_cart_failed');
});

test('a changed session token deactivates and clears the client', () => {
  let token = 'a';
  const storage = memoryStorage();
  const client = createHobbystationClient({storage, getSessionToken: () => token, request: async () => response(200, {})});
  client.activateUser('u1');
  client.saveCredentials({eccube: 'sess', eccube_bot_check: 'bot'});
  token = 'b';
  assert.throws(() => client.hasCredentials(), {message: 'authentication_required'});
  assert.equal(storage.values.size, 0);
});

function settingsSetup(query) {
  const elements = new Map();
  globalThis.document = {getElementById(id) {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, {value: '', textContent: '', className: '', hidden: false, disabled: false, attributes: {}, handlers: {},
        classList: {toggle(name, on) { on ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name)},
        setAttribute(name, value) { this.attributes[name] = value; },
        addEventListener(name, fn) { this.handlers[name] = fn; }});
    }
    return elements.get(id);
  }};
  let saved = null;
  const client = {hasCredentials: () => Boolean(saved), saveCredentials(value) { saved = value; },
    clearCredentials() { saved = null; }, query};
  const settings = bindHobbystationSettings(client);
  return {settings, el: id => document.getElementById(id), saved: () => saved};
}
const submit = el => el('hs-cart-form').handlers.submit({preventDefault() {}});

test('settings save both cookies, verify, then fold away', async () => {
  const {settings, el, saved} = settingsSetup(async () => ({total_quantity: 3}));
  settings.render();
  assert.equal(el('hs-cart-body').hidden, false);
  assert.equal(el('hs-cart-state-label').textContent, 'cart_not_configured');
  el('hs-cart-eccube').value = 'eccube=sess';
  submit(el);
  assert.equal(el('hs-cart-status').textContent, 'hs_cart_invalid_input');
  assert.equal(saved(), null);
  el('hs-cart-bot-check').value = 'bot';
  submit(el);
  assert.deepEqual(saved(), {eccube: 'sess', eccube_bot_check: 'bot'});
  assert.equal(el('hs-cart-eccube').value, '');
  assert.equal(el('hs-cart-status').textContent, 'hs_cart_verifying');
  await tick();
  assert.equal(el('hs-cart-status').textContent, 'hs_cart_verified');
  assert.equal(el('hs-cart-state-label').textContent, 'cart_configured');
  assert.equal(el('hs-cart-body').hidden, true);
  el('hs-cart-clear').handlers.click();
  assert.equal(saved(), null);
  assert.equal(el('hs-cart-state-label').textContent, 'cart_not_configured');
});

test('rejected credentials keep the panel open and ask for an update', async () => {
  const {el} = settingsSetup(async () => { const e = new Error('x'); e.detail = {type: 'hobbystation_credentials_rejected'}; throw e; });
  el('hs-cart-eccube').value = 'sess'; el('hs-cart-bot-check').value = 'bot';
  submit(el);
  await tick();
  assert.equal(el('hs-cart-status').textContent, 'hs_cart_credentials_rejected');
  assert.equal(el('hs-cart-state-label').textContent, 'cart_credentials_need_update');
  assert.equal(el('hs-cart-body').hidden, false);
  assert.equal(el('hs-cart-status').classList.contains('error'), true);
});
