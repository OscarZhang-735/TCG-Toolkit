import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = (await readFile(new URL('../src/tcg_toolkit/static/cart/yuyutei-client.js', import.meta.url), 'utf8'))
  .replace("import {apiRequest, getToken} from '../api.js';", "const apiRequest = () => {}; const getToken = () => null;");
const {createYuyuteiClient, CREDENTIALS_KEY} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const secrets = {cookie: 'session-secret', csrf_token: 'csrf-secret'};
const card = {gid: 56, ver: 'OS01', cid: '10044'};
function memory() {
  const data = new Map();
  return {getItem: key => data.get(key) ?? null, setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k)};
}
function setup({storage = memory(), request = async () => new Response('{}'), token = 'login-a'} = {}) {
  const state = {token};
  const client = createYuyuteiClient({storage, request, getSessionToken: () => state.token});
  client.activateUser('alice');
  return {client, storage, state};
}

test('sessionStorage is the default, reload preserves credentials, new tab storage does not', async () => {
  globalThis.sessionStorage = memory();
  globalThis.localStorage = {setItem() { assert.fail('No localStorage credentials'); }};
  const make = () => createYuyuteiClient({getSessionToken: () => 'login-a', request: async () => new Response('{}')});
  const first = make(); first.activateUser('alice'); first.saveCredentials(secrets);
  assert.deepEqual(JSON.parse(sessionStorage.getItem(CREDENTIALS_KEY)), {userId: 'alice', credentials: secrets});
  const reloaded = make(); reloaded.activateUser('alice');
  assert.equal(reloaded.hasCredentials(), true);
  reloaded.deactivate({clear: false}); reloaded.activateUser('alice');
  assert.equal(reloaded.hasCredentials(), true);
  globalThis.sessionStorage = memory();
  const newTab = make(); newTab.activateUser('alice');
  assert.equal(newTab.hasCredentials(), false);
  delete globalThis.localStorage;
  delete globalThis.sessionStorage;
});

test('all operations submit credentials in JSON only; add retains caller idempotency key', async () => {
  const calls = [];
  const {client} = setup({request: async (url, options) => {
    calls.push({url, options}); return new Response('{"status":"SUCCESS"}');
  }});
  client.saveCredentials(secrets);
  await client.query(); await client.add({...card, credentials: {cookie: 'override'}}, 'operation-1');
  await client.remove(card);
  assert.deepEqual(calls.map(x => [x.url, x.options.method]), [
    ['/api/yuyutei/cart/query', 'POST'], ['/api/yuyutei/cart', 'POST'], ['/api/yuyutei/cart', 'DELETE']]);
  for (const {url, options} of calls) {
    assert.deepEqual(JSON.parse(options.body).credentials, secrets);
    assert.equal(options.headers['Content-Type'], 'application/json');
    assert.equal(options.headers.Cookie, undefined);
    assert.equal(url.includes('secret'), false);
    assert.equal(options.cache, 'no-store');
  }
  assert.equal(calls[1].options.headers['Idempotency-Key'], 'operation-1');
  assert.throws(() => client.add(card), /idempotency_key_required/);
  assert.equal(calls.length, 3);
});

test('account switch and logout clear the old credentials, including switching back', () => {
  const {client, storage, state} = setup();
  client.saveCredentials(secrets);
  state.token = 'login-b'; client.activateUser('bob');
  assert.equal(client.hasCredentials(), false);
  assert.equal(storage.getItem(CREDENTIALS_KEY), null);
  state.token = 'login-a'; client.activateUser('alice');
  assert.equal(client.hasCredentials(), false);
  client.saveCredentials(secrets); client.deactivate();
  assert.equal(storage.getItem(CREDENTIALS_KEY), null);
});

test('401 and token changes clear credentials before further requests', async () => {
  let calls = 0;
  const {client, storage, state} = setup({request: async () => { calls++; return new Response('{}', {status: 401}); }});
  client.saveCredentials(secrets);
  await assert.rejects(client.query(), /authentication_required/);
  assert.equal(storage.getItem(CREDENTIALS_KEY), null);
  client.activateUser('alice'); client.saveCredentials(secrets);
  state.token = 'different-login';
  await assert.rejects(client.query(), /authentication_required/);
  assert.equal(calls, 1);
  assert.equal(storage.getItem(CREDENTIALS_KEY), null);
});

test('old response cannot clear or restore new-account credentials', async () => {
  let resolve, signal;
  const {client, storage, state} = setup({request: (url, options) => {
    signal = options.signal; return new Promise(r => { resolve = r; });
  }});
  client.saveCredentials(secrets);
  const pending = client.query();
  state.token = 'login-b'; client.activateUser('bob');
  client.saveCredentials({cookie: 'bob-cookie', csrf_token: 'bob-csrf'});
  assert.equal(signal.aborted, true);
  resolve(new Response('{}', {status: 401}));
  await assert.rejects(pending, {name: 'AbortError'});
  assert.equal(JSON.parse(storage.getItem(CREDENTIALS_KEY)).userId, 'bob');
  assert.equal(client.hasCredentials(), true);
});

test('switch during response JSON parsing also discards the old result', async () => {
  let resolveJson, entered;
  const started = new Promise(r => { entered = r; });
  const {client, state} = setup({request: async () => ({status: 200, ok: true,
    json: () => { entered(); return new Promise(r => { resolveJson = r; }); }})});
  client.saveCredentials(secrets);
  const pending = client.query(); await started;
  state.token = 'login-b'; client.activateUser('bob');
  resolveJson({items: ['old-cart']});
  await assert.rejects(pending, {name: 'AbortError'});
});

test('unavailable storage fails explicitly with no persistent fallback', async () => {
  const {client} = setup({storage: null});
  assert.throws(() => client.saveCredentials(secrets), /credential_storage_unavailable/);
  await assert.rejects(client.query(), /yuyutei_credentials_missing/);
});

test('malformed storage is cleared and unsuccessful calls are never retried', async () => {
  let calls = 0;
  const {client, storage} = setup({request: async () => {
    calls++; return new Response('{"detail":{"outcome":"unknown"}}', {status: 504});
  }});
  storage.setItem(CREDENTIALS_KEY, '{broken');
  assert.equal(client.hasCredentials(), false);
  assert.equal(storage.getItem(CREDENTIALS_KEY), null);
  client.saveCredentials(secrets);
  await assert.rejects(client.add(card, 'one-operation'), e => e.status === 504 && e.detail.outcome === 'unknown');
  assert.equal(calls, 1);
  client.clearCredentials();
  assert.equal(client.hasCredentials(), false);
});

test('reading before activation (page reload) keeps the tab credentials', () => {
  const storage = memory(), state = {token: 'login-a'};
  const make = () => createYuyuteiClient({storage, request: async () => new Response('{}'), getSessionToken: () => state.token});
  const first = make(); first.activateUser('alice'); first.saveCredentials(secrets);
  // Reload: auth.js deactivates without clearing; app.js renders before activateUser runs.
  const reloaded = make(); reloaded.deactivate({clear: false});
  assert.throws(() => reloaded.hasCredentials(), /authentication_required/);
  assert.throws(() => reloaded.saveCredentials(secrets), /authentication_required/);
  assert.notEqual(storage.getItem(CREDENTIALS_KEY), null);
  reloaded.activateUser('alice');
  assert.equal(reloaded.hasCredentials(), true);
  // A changed site login is still a reason to drop them.
  state.token = 'login-b';
  assert.throws(() => reloaded.hasCredentials(), /authentication_required/);
  assert.equal(storage.getItem(CREDENTIALS_KEY), null);
});
