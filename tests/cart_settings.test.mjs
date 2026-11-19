import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const base = new URL('../src/tcg_toolkit/static/', import.meta.url);
const moduleURL = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const parserURL = moduleURL(await readFile(new URL('cart/curl-credentials.js', base), 'utf8'));
const {bindCartSettings} = await import(moduleURL((await readFile(new URL('cart/cart-settings.js', base), 'utf8'))
  .replace("import {t, getLanguage} from '../i18n.js';", "const t = key => key, getLanguage = () => 'en';")
  .replace("'./curl-credentials.js'", JSON.stringify(parserURL))));

function setup() {
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
  let stored = false;
  const client = {hasCredentials: () => stored, saveCredentials() { stored = true; }, clearCredentials() { stored = false; }};
  const cart = {state: {loading: false, error: '', rows: [], totalQuantity: null, complete: true},
    refresh() { this.state.loading = true; }, clear() { this.state.totalQuantity = null; }};
  const settings = bindCartSettings(client, cart);
  const el = id => document.getElementById(id);
  const finish = (patch) => { Object.assign(cart.state, {loading: false}, patch); settings.render(); };
  return {client, cart, settings, el, finish};
}

test('unconfigured panel starts open and can still be collapsed', () => {
  const {settings, el} = setup();
  settings.render();
  assert.equal(el('cart-settings-body').hidden, false);
  assert.equal(el('cart-settings-toggle').hidden, false);
  assert.equal(el('cart-settings-state-label').textContent, 'cart_not_configured');
  assert.equal(el('cart-clear').hidden, true);
  el('cart-settings-toggle').handlers.click();
  assert.equal(el('cart-settings-body').hidden, true);
  assert.equal(el('cart-settings-toggle-icon').className, 'bi bi-chevron-down');
  settings.render();
  assert.equal(el('cart-settings-body').hidden, true, 'Stays collapsed on later renders');
});

test('successful verification collapses the panel with a green badge; the toggle reopens it', () => {
  const {settings, el, finish} = setup();
  el('cart-cookie').value = 'c'; el('cart-csrf').value = 't';
  el('cart-settings-form').handlers.submit({preventDefault() {}});
  settings.render();
  assert.equal(el('cart-settings-body').hidden, false, 'Open while verifying');
  finish({totalQuantity: 3});
  assert.equal(el('cart-clear').hidden, false);
  assert.equal(el('cart-settings-body').hidden, true);
  assert.equal(el('cart-settings-state').classList.contains('ok'), true);
  assert.equal(el('cart-settings-state-label').textContent, 'cart_configured');
  assert.equal(el('cart-settings-toggle-icon').className, 'bi bi-chevron-down');
  assert.equal(el('cart-settings-toggle').attributes['aria-label'], 'cart_settings_edit');
  el('cart-settings-toggle').handlers.click();
  assert.equal(el('cart-settings-body').hidden, false);
  assert.equal(el('cart-settings-toggle').attributes['aria-expanded'], 'true');
  assert.equal(el('cart-settings-toggle-icon').className, 'bi bi-chevron-up');
});

test('rejected credentials reopen the panel; cURL import verifies like option 1', () => {
  const {settings, el, finish, client} = setup();
  client.saveCredentials();
  finish({totalQuantity: 0});
  assert.equal(el('cart-settings-body').hidden, true, 'Reload with working credentials starts folded');
  finish({error: 'cart_credentials_rejected', totalQuantity: null});
  assert.equal(el('cart-settings-body').hidden, false);
  assert.equal(el('cart-settings-state-label').textContent, 'cart_credentials_need_update');
  el('cart-curl').value = "curl 'https://yuyu-tei.jp' -b 'laravel_session=s' -H 'X-CSRF-TOKEN: t'";
  finish({error: ''});
  el('cart-curl-save').handlers.click();
  settings.render();
  assert.equal(el('cart-settings-body').hidden, false, 'Open while verifying');
  assert.equal(el('cart-curl').value, '');
  finish({totalQuantity: 2});
  assert.equal(el('cart-settings-body').hidden, true);
  assert.equal(el('cart-settings-state').classList.contains('ok'), true);
  assert.equal(el('cart-settings-status').textContent, 'cart_verified');
});
