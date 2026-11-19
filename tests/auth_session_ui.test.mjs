import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
import {runInNewContext} from 'node:vm';

const source = (await readFile(new URL('../src/tcg_toolkit/static/auth.js', import.meta.url), 'utf8'))
  .replace(/^import .*;\r?\n/gm, '');
const noop = () => {};
async function mount(initialToken = null, initialResponse) {
  let token = initialToken, request = async () => initialResponse;
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value: '', textContent: '', type: 'password', hidden: false,
      listeners: {}, attributes: {}, firstElementChild: {}, classList: {toggle: noop},
      addEventListener(name, listener) {this.listeners[name] = listener;},
      setAttribute(name, value) {this.attributes[name] = value;},
      getAttribute(name) {return this.attributes[name];}, removeAttribute: noop, close: noop});
    return nodes.get(id);
  };
  const ui = {refresh: noop, reset: noop};
  await runInNewContext(`(async () => {${source}\n})()`, {
    document: {getElementById: node, body: node('body'), querySelectorAll: () => []},
    window: {addEventListener: noop}, location: {hash: '#login'}, setInterval: noop,
    apiRequest: (...args) => request(...args), getToken: () => token, setToken: value => {token = value;}, TOKEN_KEY: 'tcg.session',
    t: key => key, applyLanguage: noop, setLanguage: noop, getLanguage: () => 'en',
    createAuthenticatedRequest: () => ({invalidate: noop, fetch: (...args) => request(...args)}),
    yuyuteiClient: {deactivate: noop, clearCredentials: noop}, hobbystationClient: {deactivate: noop, clearCredentials: noop},
    cardmaxClient: {deactivate: noop, clearCredentials: noop}, bindNavMenus: noop, syncNavMenus: noop,
    createEmailChallenge: () => ({reset: noop, snapshot: () => ({}), payload: () => null}), authError: key => key,
    createPasswordUI: () => ui, createSubscriptionSettingsUI: () => ui, createNotificationEmailUI: () => ui,
    createAvatarCropper: () => ({}), loadImage: noop,
  });
  return {node, getToken: () => token, setToken: value => {token = value;},
    deferRequest() {let resolve; request = () => new Promise(r => {resolve = r;}); return value => resolve(value);}};
}

test('a delayed login response does not overwrite a session established by another tab', async () => {
  const view = await mount();
  const respond = view.deferRequest();
  const pending = view.node('auth-form').listeners.submit({preventDefault: noop});
  view.setToken('another-tab-session');
  respond({ok: true, json: async () => ({token: 'old-login-response', user: {id: 'alice'}})});
  await pending;
  assert.equal(view.getToken(), 'another-tab-session');
  assert.equal(view.node('auth-error').textContent, '');
});

test('a delayed logout response does not clear a session established by another tab', async () => {
  const view = await mount();
  view.setToken('old-session');
  const respond = view.deferRequest();
  const pending = view.node('logout').listeners.click();
  view.setToken('another-tab-session');
  respond({ok: true});
  await pending;
  assert.equal(view.getToken(), 'another-tab-session');
});

test('restoring an invalidated session explains why sign-in is required', async () => {
  const view = await mount('invalidated-session', {ok: false, status: 401});
  assert.equal(view.getToken(), null);
  assert.equal(view.node('auth-error').textContent, 'auth_expired');
});
