import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const noticesModule = 'data:text/javascript;base64,' + Buffer.from(await readFile(new URL('../src/tcg_toolkit/static/notices.js', import.meta.url), 'utf8')).toString('base64');
const source = (await readFile(new URL('../src/tcg_toolkit/static/cart.js', import.meta.url), 'utf8')).replace("'./notices.js'", JSON.stringify(noticesModule));
const {cartTarget, cartErrorKey, createCart, parseQuantity, quantityRange, planQuantity} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const target = {gid: 56, ver: 'OS01', cid: '10044', kizu: 0};
const row = (quantity, type = 'normal', extra = {}) => ({...target, type, quantity, unit_price: 30, ...extra});
const cartOf = items => ({items, total_quantity: items.reduce((s, i) => s + i.quantity, 0),
  subtotal: items.reduce((s, i) => s + i.quantity * i.unit_price, 0), complete: true, warnings: []});
function fakeClient(overrides = {}) {
  const calls = [];
  return {calls, hasCredentials: () => true,
    query: async () => { calls.push(['query']); return cartOf([row(2)]); },
    add: async (card, key) => { calls.push(['add', card, key]); return {after_quantity: 3}; },
    setQuantity: async (card, quantity) => { calls.push(['set', card, quantity]); return {cart: cartOf(quantity ? [row(quantity)] : [])}; },
    ...overrides};
}

test('cart target prefers hidden fields and falls back to the product URL', () => {
  assert.deepEqual(cartTarget({game: 'sev', product_url: 'https://yuyu-tei.jp/sell/sev/card/bp01/10238'}),
    {gid: 47, ver: 'bp01', cid: '10238', kizu: 0});
  assert.deepEqual(cartTarget({cart: {...target, kizu: 1}}), {...target, kizu: 1});
  assert.deepEqual(cartTarget({game: 'lycee', product_url: 'https://yuyu-tei.jp/sell/lo/card/bd5.0/123/'}),
    {gid: 34, ver: 'bd5.0', cid: '123', kizu: 0});
  assert.equal(cartTarget({game: 'ws', product_url: 'https://yuyu-tei.jp/sell/pm/card/a/1'}), null);
  assert.equal(cartTarget({game: 'ws', product_url: 'https://evil.example/sell/ws/card/a/1'}), null);
});

test('refresh reads quantities; unconfigured cart sends nothing', async () => {
  const client = fakeClient();
  const cart = createCart(client);
  await cart.refresh();
  assert.equal(cart.quantity(target), 2);
  assert.equal(cart.state.totalQuantity, 2);
  const empty = createCart(fakeClient({hasCredentials: () => { throw new Error('authentication_required'); },
    query: async () => assert.fail('No query without credentials')}));
  await empty.refresh();
  assert.equal(empty.state.configured, false);
});

test('increase adds one with a fresh idempotency key and keeps reserved rows separate', async () => {
  const client = fakeClient({query: async () => cartOf([row(2), row(1, 'reserved')]),
    add: async (card, key) => { client.calls.push(['add', card, key]); return {after_quantity: 4}; }});
  let n = 0;
  const cart = createCart(client, () => {}, () => `key-${++n}`);
  await cart.refresh();
  await cart.increase(target, {price: 30});
  assert.deepEqual(client.calls.at(-1), ['add', {...target, counter: 1}, 'key-1']);
  assert.equal(cart.quantity(target), 4);
  assert.equal(cart.state.subtotal, 120);
  await cart.increase({...target, cid: '1'}, {price: 50, name: 'New', game: 'lycee'});
  assert.equal(client.calls.at(-1)[2], 'key-2');
});

test('decrease lowers the normal row to an absolute target', async () => {
  const client = fakeClient();
  const cart = createCart(client);
  await cart.refresh();
  await cart.decrease(target);
  assert.deepEqual(client.calls.at(-1), ['set', {...target, type: 'normal'}, 1]);
  assert.equal(cart.quantity(target), 1);
  await cart.decrease(target);
  assert.equal(cart.quantity(target), 0);
  const before = client.calls.length;
  await cart.decrease(target);
  assert.equal(client.calls.length, before, 'Nothing to lower');
});

test('uncertain failures show a message and re-read the cart; double clicks are ignored', async () => {
  let release;
  const client = fakeClient({add: () => new Promise((_, reject) => { release = () => {
    const error = new Error('cart_request_failed'); error.status = 504;
    error.detail = {type: 'yuyutei_timeout', outcome: 'unknown'}; reject(error);
  }; })});
  const cart = createCart(client, () => {}, () => 'key');
  await cart.refresh();
  const first = cart.increase(target);
  await cart.increase(target);
  assert.equal(cart.isBusy(target), true);
  release(); await first;
  assert.equal(cart.message(target), 'cart_unverified');
  assert.deepEqual(client.calls.map(c => c[0]), ['query', 'query']);
});

test('error keys never expose upstream text', () => {
  assert.equal(cartErrorKey({detail: {type: 'yuyutei_cart_rejected', code: 'ERROR1', outcome: 'partial'}}), 'cart_partial');
  assert.equal(cartErrorKey({detail: {type: 'yuyutei_credentials_rejected'}}), 'cart_credentials_rejected');
  assert.equal(cartErrorKey({detail: 'invalid_yuyutei_request'}), 'cart_invalid_credentials');
  assert.equal(cartErrorKey({message: 'yuyutei_credentials_missing'}), 'cart_need_credentials');
  assert.equal(cartErrorKey({detail: {type: 'something_new'}}), 'cart_failed');
});

test('clear drops replies from the previous account', async () => {
  let resolve;
  const cart = createCart(fakeClient({query: () => new Promise(r => { resolve = r; })}));
  const pending = cart.refresh();
  cart.clear();
  resolve(cartOf([row(5)])); await pending;
  assert.equal(cart.quantity(target), 0);
  assert.equal(cart.state.loading, false);
});


test('credential import resets stale cart and enables refresh without querying', () => {
  const client = fakeClient();
  const cart = createCart(client);
  cart.clear({syncCredentials: true});
  assert.equal(cart.state.configured, true);
  assert.equal(cart.state.totalQuantity, null);
  assert.deepEqual(client.calls, []);
});

test('a product new to the cart keeps the search details for the cart page', async () => {
  const client = fakeClient({query: async () => cartOf([]),
    add: async () => ({after_quantity: 1})});
  const cart = createCart(client, () => {}, () => 'key');
  await cart.refresh();
  await cart.increase(target, {price: 30, name: 'Card', game: 'lycee', product_url: 'https://yuyu-tei.jp/sell/lo/card/OS01/10044'});
  assert.deepEqual(cart.state.rows.map(r => [r.name, r.game, r.quantity, r.type]), [['Card', 'lo', 1, 'normal']]);
});

test('adding to a product that only has reserved rows re-reads the cart', async () => {
  const client = fakeClient({query: async () => { client.calls.push(['query']); return cartOf([row(1, 'reserved')]); }});
  const cart = createCart(client, () => {}, () => 'key');
  await cart.refresh();
  await cart.increase(target);
  assert.deepEqual(client.calls.map(c => c[0]), ['query', 'add', 'query']);
  assert.equal(cart.state.rows.length, 1);
});

test('cart page lowers or removes the exact row type', async () => {
  const removed = [];
  const client = fakeClient({query: async () => cartOf([row(2), row(3, 'reserved')]),
    remove: async card => { removed.push(card); return {cart: cartOf([row(2)])}; }});
  const cart = createCart(client);
  await cart.refresh();
  await cart.decrease(target, 'reserved');
  assert.deepEqual(client.calls.at(-1), ['set', {...target, type: 'reserved'}, 2]);
  await cart.refresh();
  await cart.remove(target, 'reserved');
  assert.deepEqual(removed, [{...target, type: 'reserved'}]);
  assert.equal(cart.quantity(target), 2);
});


test('typed quantities: whole numbers only, full-width digits accepted', () => {
  for (const [text, value] of [['3', 3], [' 12 ', 12], ['007', 7], ['０', 0], ['１２', 12], ['0', 0]]) assert.equal(parseQuantity(text), value, text);
  for (const text of ['', ' ', '-1', '1.5', '1e3', '+2', 'abc', '3张', '1 2', '1234567890', null, undefined]) assert.equal(parseQuantity(text), null, String(text));
});

test('range: stock caps the product, 99 caps one add, pre-orders set the floor', () => {
  assert.deepEqual(quantityRange([], {stock: 4}), {row: undefined, current: 0, others: 0, min: 0, max: 4, stockLimited: true});
  assert.equal(quantityRange([], {}).max, 99, 'Unknown stock: one request adds at most 99');
  assert.equal(quantityRange([row(5)], {stock: 500}).max, 104);
  assert.equal(quantityRange([row(5)], {stock: 3}).max, 5, 'Stock fell below the cart: never forces a decrease');
  assert.equal(quantityRange([], {stock: 0}).max, 0, 'Sold out');
  const mixed = quantityRange([row(2), row(3, 'reserved')]);
  assert.deepEqual([mixed.current, mixed.others, mixed.min], [5, 3, 3]);
  const fixed = quantityRange([row(4, 'normal', {removable: false})]);
  assert.deepEqual([fixed.min, fixed.max], [4, 103], 'No removal control: can only grow');
  const reservedRow = quantityRange([row(2), row(3, 'reserved')], {type: 'reserved'});
  assert.deepEqual([reservedRow.current, reservedRow.others, reservedRow.min], [3, 0, 0], 'Cart page edits one row');
});

test('plan: out-of-range numbers are clamped with a note, never sent as is', () => {
  const r = quantityRange([row(2)], {stock: 4});
  assert.deepEqual(planQuantity(r, 3), {value: 3, note: null});
  assert.deepEqual(planQuantity(r, 9), {value: 4, note: {key: 'cart_qty_max_stock', params: {max: 4}}});
  assert.deepEqual(planQuantity(quantityRange([row(2)]), 500), {value: 101, note: {key: 'cart_qty_max', params: {max: 101, step: 99}}});
  assert.deepEqual(planQuantity(quantityRange([row(2), row(3, 'reserved')]), 1), {value: 3, note: {key: 'cart_qty_min', params: {min: 3}}});
  assert.deepEqual(planQuantity(quantityRange([row(4, 'normal', {removable: false})]), 0), {value: 4, note: {key: 'cart_qty_cannot_lower', params: {min: 4}}});
  // 150 in one row: lowering re-adds the target, which may not exceed 98.
  assert.deepEqual(planQuantity(quantityRange([row(150)]), 120), {value: 150, note: {key: 'cart_qty_lower_limit', params: {max: 98}}});
  assert.deepEqual(planQuantity(quantityRange([row(150)]), 98), {value: 98, note: null});
});

test('setTo adds the difference, lowers the right row, or sends nothing', async () => {
  const client = fakeClient({query: async () => cartOf([row(2)]),
    add: async (card, key) => { client.calls.push(['add', card, key]); return {after_quantity: 2 + card.counter}; }});
  const cart = createCart(client, () => {}, () => 'key');
  await cart.refresh();
  await cart.setTo(target, 2);
  assert.equal(client.calls.filter(c => c[0] !== 'query').length, 0, 'Unchanged: no request');
  await cart.setTo(target, 7, {stock: 10});
  assert.deepEqual(client.calls.at(-1), ['add', {...target, counter: 5}, 'key']);
  assert.equal(cart.quantity(target), 7);
  await cart.setTo(target, 0);
  assert.deepEqual(client.calls.at(-1), ['set', {...target, type: 'normal'}, 0]);
  assert.equal(cart.quantity(target), 0);
});

test('setTo clamps to stock and explains why', async () => {
  const client = fakeClient({query: async () => cartOf([row(1)]),
    add: async card => { client.calls.push(['add', card]); return {after_quantity: 1 + card.counter}; }});
  const cart = createCart(client, () => {}, () => 'key');
  await cart.refresh();
  assert.equal(await cart.setTo(target, 50, {stock: 4}), 4);
  assert.deepEqual(client.calls.at(-1), ['add', {...target, counter: 3}]);
  assert.deepEqual(cart.message(target), {key: 'cart_qty_max_stock', params: {max: 4}});
  // Already at the stock limit: nothing more is sent.
  const before = client.calls.length;
  await cart.setTo(target, 6, {stock: 4});
  assert.equal(client.calls.length, before);
});

test('a failed request keeps its error rather than the clamp note', async () => {
  const client = fakeClient({add: async () => { const e = new Error('cart_request_failed'); e.status = 409; e.detail = {type: 'yuyutei_cart_rejected'}; throw e; }});
  const cart = createCart(client, () => {}, () => 'key');
  await cart.refresh();
  await cart.setTo(target, 50, {stock: 9});
  assert.equal(cart.message(target), 'cart_rejected');
});


test('a view that throws while drawing cannot lock a product as busy', async () => {
  let fail = false;
  const client = fakeClient();
  const cart = createCart(client, () => { if (fail) throw new Error('view bug'); }, () => 'key');
  await cart.refresh();
  const quiet = console.error; console.error = () => {};
  try {
    fail = true;
    await cart.setTo(target, 0);
    assert.deepEqual(client.calls.at(-1), ['set', {...target, type: 'normal'}, 0]);
    assert.equal(cart.isBusy(target), false);
    assert.equal(cart.quantity(target), 0);
  } finally { console.error = quiet; }
});

test('stock cap rolls back partial additions and shows the warning', async () => {
  let qty = 2;
  const client = fakeClient({query: async () => cartOf([row(qty)]),
    add: async () => { qty = 4; throw Object.assign(new Error('cart_request_failed'), {status: 409, detail: {
      type: 'yuyutei_quantity_mismatch', requested_quantity: 5, before_quantity: 2, after_quantity: 4}}); },
    setQuantity: async (target, value) => { assert.equal(value, 2); qty = value; return {cart: cartOf([row(qty)])}; }});
  const cart = createCart(client, () => {}, () => 'key');
  await cart.refresh(); await cart.setTo(target, 7);
  assert.equal(qty, 2); assert.equal(cart.quantity(target), 2);
  assert.equal(cart.message(target), 'cart_limit_reached');
});

test('unchanged stock cap restores the number without a compensating write', async () => {
  const client = fakeClient({add: async () => { throw Object.assign(new Error('cart_request_failed'), {status: 409, detail: {
    type: 'yuyutei_quantity_mismatch', requested_quantity: 1, before_quantity: 2, after_quantity: 2}}); },
    setQuantity: async () => assert.fail('no rollback required')});
  const cart = createCart(client, () => {}, () => 'key');
  await cart.refresh(); await cart.increase(target);
  assert.equal(cart.quantity(target), 2); assert.equal(cart.message(target), 'cart_limit_reached');
});

test('failed rollback shows actual quantity and retains failure instead of claiming restoration', async () => {
  let qty = 2;
  const client = fakeClient({query: async () => cartOf([row(qty)]),
    add: async () => { qty = 4; throw Object.assign(new Error('cart_request_failed'), {status: 409, detail: {
      type: 'yuyutei_quantity_mismatch', requested_quantity: 5, before_quantity: 2, after_quantity: 4}}); },
    setQuantity: async () => { throw Object.assign(new Error('cart_request_failed'), {status: 504, detail: {type: 'yuyutei_timeout'}}); }});
  const cart = createCart(client, () => {}, () => 'key');
  await cart.refresh(); await cart.setTo(target, 7);
  assert.equal(cart.quantity(target), 4); assert.equal(cart.message(target), 'cart_unverified');
});

test('limit bubble keeps its start time across refresh and expires after the fade', async t => {
  const clock = fakeWarningClock(t);
  const client = fakeClient({add: async () => { throw Object.assign(new Error('cart_request_failed'), {status: 409, detail: {
    type: 'yuyutei_quantity_mismatch', requested_quantity: 1, before_quantity: 2, after_quantity: 2}}); }});
  const cart = createCart(client, () => {}, () => 'key');
  await cart.refresh(); await cart.increase(target);
  const started = cart.bubbleStarted(target);
  clock.tick(2000);
  await cart.refresh();
  assert.equal(cart.bubbleStarted(target), started);
  assert.equal(cart.message(target), 'cart_limit_reached');
  clock.tick(300);
  assert.equal(cart.message(target), undefined);
  assert.equal(cart.bubbleStarted(target), undefined);
});

test('a new warning gets its own two seconds and logout cancels expiration', async t => {
  const clock = fakeWarningClock(t);
  const client = fakeClient({add: async () => { throw Object.assign(new Error('cart_request_failed'), {status: 409, detail: {
    type: 'yuyutei_quantity_mismatch', requested_quantity: 1, before_quantity: 2, after_quantity: 2}}); }});
  let renders = 0;
  const cart = createCart(client, () => { renders++; }, () => 'key');
  await cart.refresh(); await cart.increase(target);
  clock.tick(1800); await cart.increase(target);
  clock.tick(500);
  assert.equal(cart.message(target), 'cart_limit_reached');
  cart.clear(); const previous = renders;
  clock.tick(3000);
  assert.equal(renders, previous);
  assert.equal(cart.bubbleStarted(target), undefined);
});


function fakeWarningClock(t) {
  const original = {setTimeout, clearTimeout, now: Date.now};
  let now = 10000, next = 0;
  const tasks = new Map();
  globalThis.setTimeout = (callback, delay) => { const id = ++next; tasks.set(id, {callback, at: now + delay}); return id; };
  globalThis.clearTimeout = id => tasks.delete(id);
  Date.now = () => now;
  t.after(() => { globalThis.setTimeout = original.setTimeout; globalThis.clearTimeout = original.clearTimeout; Date.now = original.now; });
  return {tick(ms) { now += ms; for (const [id, task] of tasks) if (task.at <= now) { tasks.delete(id); task.callback(); } }};
}

test('handled outcomes fade with their bubble; errors stay as a dot until the next action', async t => {
  const clock = fakeWarningClock(t);
  let fail = {status: 504, detail: {type: 'yuyutei_timeout'}};
  const client = fakeClient({add: async () => { if (fail) throw Object.assign(new Error('cart_request_failed'), fail); return {after_quantity: 3}; }});
  const cart = createCart(client, () => {}, () => 'key');
  await cart.refresh(); await cart.increase(target);
  assert.equal(cart.message(target), 'cart_unverified');
  clock.tick(2300);
  assert.equal(cart.message(target), undefined);
  fail = {status: 422, detail: {type: 'yuyutei_cart_rejected'}};
  await cart.increase(target);
  assert.equal(cart.message(target), 'cart_rejected'); assert.notEqual(cart.bubbleStarted(target), undefined);
  clock.tick(2300);
  assert.equal(cart.message(target), 'cart_rejected'); assert.equal(cart.bubbleStarted(target), undefined);
  fail = null; await cart.increase(target);
  assert.equal(cart.message(target), undefined);
});

test('credential problems become one page notice instead of a per-card message', async () => {
  let fail = true;
  const client = fakeClient({add: async () => {
    if (fail) throw Object.assign(new Error('cart_request_failed'), {status: 401, detail: {type: 'yuyutei_credentials_rejected'}});
    return {after_quantity: 3};
  }});
  const cart = createCart(client, () => {}, () => 'key');
  await cart.refresh(); await cart.increase(target);
  assert.equal(cart.state.notice, 'cart_credentials_rejected');
  assert.equal(cart.message(target), undefined);
  cart.dismissNotice(); assert.equal(cart.state.notice, '');
  await cart.increase(target); assert.equal(cart.state.notice, 'cart_credentials_rejected');
  fail = false; await cart.increase(target);
  assert.equal(cart.state.notice, '');
});
