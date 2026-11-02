import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const source = await readFile(new URL('../src/tcg_toolkit/static/subscriptions.js', import.meta.url));
const {createSubscriptions, subscriptionKey} = await import(`data:text/javascript;base64,${source.toString('base64')}`);
const item = {game: 'wsr', product_url: 'https://yuyu-tei.jp/sell/wsr/card/OS01/10240/', name: 'Card'};
const row = {id: 'one', item: {...item, product_url: 'https://yuyu-tei.jp/sell/wsr/card/OS01/10240'}, checked_at: 1, last_error: null};
const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});

test('subscribe sends only the product location and matches the normalized URL', async () => {
  const bodies = [];
  const model = createSubscriptions(() => {});
  await model.activate(async (url, options) => {
    if (options.method === 'GET') return response({items: []});
    bodies.push([url, options.method, JSON.parse(options.body)]);
    return options.method === 'POST' ? response(row) : response(null, 204);
  });
  assert.equal(await model.toggle(item), true);
  assert.deepEqual(bodies[0], ['/api/subscriptions', 'POST', {game: 'wsr', product_url: item.product_url}]);
  assert.equal(subscriptionKey(item), subscriptionKey(row.item));
  assert.deepEqual(model.find(item), row);
  assert.equal(await model.toggle(item), true);
  assert.deepEqual(bodies[1], ['/api/subscriptions/one', 'DELETE', {}]);
  assert.equal(model.find(item), undefined);
});

test('repeated clicks cannot duplicate requests', async () => {
  let resolve, writes = 0;
  const model = createSubscriptions(() => {});
  await model.activate(async (url, options) => options.method === 'GET' ? response({items: []}) : (writes++, new Promise(r => {resolve = r;})));
  const pending = model.toggle(item);
  assert.equal(await model.toggle(item), false);
  assert.equal(writes, 1);
  resolve(response(row)); assert.equal(await pending, true);
});

test('server errors become per-card messages and keep state', async () => {
  const cases = [[403, 'verified_email_required', 'subscription_email_required'], [504, 'product_timeout', 'subscription_timeout'],
    [502, 'product_unreadable', 'subscription_unreadable'], [422, [{msg: 'bad'}], 'subscription_unsupported'], [500, null, 'subscription_failed']];
  for (const [status, detail, key] of cases) {
    const model = createSubscriptions(() => {});
    await model.activate(async (url, options) => options.method === 'GET' ? response({items: []}) : response({detail}, status));
    assert.equal(await model.toggle(item), false);
    assert.equal(model.state.messages.get(subscriptionKey(item)), key);
    assert.deepEqual(model.state.records, []); assert.equal(model.state.busy.size, 0);
  }
});

test('remove succeeds when the subscription is already gone', async () => {
  const model = createSubscriptions(() => {});
  await model.activate(async (url, options) => options.method === 'GET' ? response({items: [row]}) : response({detail: 'subscription_not_found'}, 404));
  assert.equal(await model.remove(item), true);
  assert.deepEqual(model.state.records, []);
  assert.equal(await model.remove(item), true);
});

test('load failure is reported and blocks writes', async () => {
  let writes = 0;
  const model = createSubscriptions(() => {});
  await model.activate(async (url, options) => options.method === 'GET' ? response({}, 500) : (writes++, response(row)));
  assert.equal(model.state.error, 'subscription_load_failed');
  assert.equal(await model.toggle(item), false); assert.equal(writes, 0);
});

test('old responses cannot restore previous account data', async () => {
  let resolve;
  const model = createSubscriptions(() => {});
  const pending = model.activate(async () => ({ok: true, status: 200, json: () => new Promise(r => {resolve = r;})}));
  await Promise.resolve();
  await model.activate(async () => response({items: []}));
  resolve({items: [row]}); await pending;
  assert.deepEqual(model.state.records, []);
});
