import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const base = new URL('../src/tcg_toolkit/static/', import.meta.url);
const asModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const code = asModule(await readFile(new URL('email-code.js', base), 'utf8'));
const i18n = asModule(`export const t = (key, p = {}) => p.email ? key + ':' + p.email : key; export const getLanguage = () => 'zh-CN';`);
const source = (await readFile(new URL('notification-email-ui.js', base), 'utf8'))
  .replace("'./email-code.js'", JSON.stringify(code)).replace("'./i18n.js'", JSON.stringify(i18n));
const {createNotificationEmailUI} = await import(asModule(source));
const reply = (status, body) => ({status, ok: status < 300, headers: {get: () => null}, json: async () => body});
const registration = {email: 'alice@example.com', custom_email: null, using_registration_email: true};
const custom = {email: 'alerts@example.com', custom_email: 'alerts@example.com', using_registration_email: false};

function setup(handler) {
  const fields = new Map(), calls = [], reported = [];
  const $ = id => {
    if (!fields.has(id)) fields.set(id, {value: '', hidden: false, disabled: false, textContent: '', className: '',
      listeners: {}, attributes: {}, classes: new Set(),
      classList: {toggle(name, on) { on ? fields.get(id).classes.add(name) : fields.get(id).classes.delete(name); }},
      setAttribute(name, value) { this.attributes[name] = value; }, getAttribute(name) { return this.attributes[name]; },
      addEventListener(name, listener) { this.listeners[name] = listener; }, reportValidity() { return true; },
      reset() { for (const key of ['notification-new-email', 'notification-code']) $(key).value = ''; }});
    return fields.get(id);
  };
  const request = async (path, options = {}) => {
    calls.push([path, options.method || 'GET', options.body ? JSON.parse(options.body) : null]);
    return handler(path, options);
  };
  const ui = createNotificationEmailUI({request, onChange: email => reported.push(email), root: {getElementById: $}});
  const fire = async (id, name) => { await fields.get(id).listeners[name]?.({preventDefault() {}}); await new Promise(r => setTimeout(r)); };
  return {ui, $, calls, reported, fire};
}

test('load shows the registration address and reports it to the favorites page', async () => {
  const {ui, $, reported} = setup(async () => reply(200, registration));
  await ui.activate();
  assert.equal($('notification-email').textContent, 'notify_to_registration:alice@example.com');
  assert.equal($('notification-state-label').textContent, 'notify_state_registration');
  assert.equal($('notification-reset').hidden, true);
  assert.deepEqual(reported, ['alice@example.com']);
});

test('verify a custom address with a code, then switch back', async () => {
  const {ui, $, calls, reported, fire} = setup(async (path, {method = 'GET'}) =>
    path.endsWith('/code') ? reply(200, {challenge_id: 'c1', expires_in: 600, retry_after: 60})
      : method === 'PUT' ? reply(200, custom) : reply(200, registration));
  await ui.activate();
  await fire('notification-toggle', 'click');
  assert.equal($('notification-form').hidden, false);
  $('notification-new-email').value = 'alerts@example.com';
  await fire('notification-new-email', 'input');
  await fire('notification-send-code', 'click');
  assert.deepEqual(calls[1], ['/api/subscriptions/notification-email/code', 'POST', {email: 'alerts@example.com', language: 'zh-CN'}]);
  $('notification-code').value = '123456';
  await fire('notification-form', 'submit');
  assert.deepEqual(calls[2], ['/api/subscriptions/notification-email', 'PUT', {challenge_id: 'c1', code: '123456'}]);
  assert.equal($('notification-email').textContent, 'notify_to_custom:alerts@example.com');
  assert.equal($('notification-form').hidden, true);
  assert.equal($('notification-reset').hidden, false);
  // Without a verified account email (no getUser here) the reset only removes the custom address.
  assert.equal($('notification-reset-label').textContent, 'notify_remove_custom');
  await fire('notification-reset', 'click');
  assert.deepEqual(calls[3], ['/api/subscriptions/notification-email', 'DELETE', {}]);
  assert.deepEqual(reported, ['alice@example.com', 'alerts@example.com', 'alice@example.com']);
});

test('submitting without a code asks for one; server errors keep the current address', async () => {
  const {ui, $, calls, reported, fire} = setup(async (path, {method = 'GET'}) =>
    path.endsWith('/code') ? reply(200, {challenge_id: 'c1', expires_in: 600, retry_after: 60})
      : method === 'PUT' ? reply(400, {detail: 'invalid_code'}) : reply(200, registration));
  await ui.activate();
  await fire('notification-form', 'submit');
  assert.equal($('notification-error').textContent, 'email_request_code');
  assert.equal(calls.length, 1);
  $('notification-new-email').value = 'alerts@example.com';
  await fire('notification-send-code', 'click');
  $('notification-code').value = '000000';
  await fire('notification-form', 'submit');
  assert.equal($('notification-error').textContent, 'email_invalid_code');
  assert.equal($('notification-email').textContent, 'notify_to_registration:alice@example.com');
  assert.deepEqual(reported, ['alice@example.com']);
});

test('no verified address and failed loads are reported', async () => {
  let status = 500;
  const {ui, $, reported, fire} = setup(async () => reply(status, {email: null, custom_email: null, using_registration_email: true}));
  await ui.activate();
  assert.equal($('notification-email').textContent, 'notify_load_failed');
  assert.equal($('notification-retry').hidden, false);
  status = 200;
  await fire('notification-retry', 'click');
  assert.equal($('notification-email').textContent, 'notify_none');
  assert.equal($('notification-state-label').textContent, 'notify_state_none');
  assert.deepEqual(reported, [null]);
});

test('a reply that arrives after sign-out is ignored', async () => {
  let resolve;
  const {ui, $, reported} = setup(() => new Promise(r => { resolve = r; }));
  const pending = ui.activate();
  ui.reset();
  resolve(reply(200, registration)); await pending;
  assert.deepEqual(reported, []);
  assert.equal($('notification-email').textContent, 'notify_loading');
});
