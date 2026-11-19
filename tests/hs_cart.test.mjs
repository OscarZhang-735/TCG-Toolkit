import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const base = new URL('../src/tcg_toolkit/static/', import.meta.url);
const asModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const read = name => readFile(new URL(name, base), 'utf8');
const noticesModule = asModule(await read('notices.js'));
const identityModule = asModule(await read('product-identity.js'));
const cartModule = asModule((await read('cart.js'))
  .replace("'./notices.js'", JSON.stringify(noticesModule)).replace("'./product-identity.js'", JSON.stringify(identityModule)));
const {hobbystationTarget, hobbystationCartErrorKey, createHobbystationCart} = await import(asModule((await read('hs-cart.js'))
  .replace("'./cart.js'", JSON.stringify(cartModule))));
const {messageTier} = await import(noticesModule);

const URL_LO = 'https://www.hobbystation-single.jp/lo/product/detail/305273';
const hsRow = (quantity, extra = {}) => ({product_id: '305273', product_class_id: '9001', product_url: URL_LO,
  name: 'Card', image_url: null, quantity, unit_price: 120, currency: 'JPY', removable: true, ...extra});
const cartOf = items => ({items, total_quantity: items.reduce((s, i) => s + i.quantity, 0),
  subtotal: items.reduce((s, i) => s + i.quantity * i.unit_price, 0), currency: 'JPY', complete: true, warnings: []});

function fakeClient(initial = []) {
  let rows = initial;
  const calls = [];
  return {calls, hasCredentials: () => true,
    query: async () => { calls.push(['query']); return cartOf(rows); },
    add: async (body, key) => {
      calls.push(['add', body, key]);
      const before = rows[0]?.quantity ?? 0;
      rows = [hsRow(before + body.counter)];
      return {status: 'SUCCESS', before_quantity: before, after_quantity: before + body.counter, cart: cartOf(rows)};
    },
    setQuantity: async (sku, quantity) => {
      calls.push(['set', sku, quantity]);
      rows = quantity ? [hsRow(quantity)] : [];
      return {status: 'SUCCESS', cart: cartOf(rows)};
    },
    remove: async sku => { calls.push(['remove', sku]); rows = []; return {status: 'SUCCESS', cart: cartOf(rows)}; }};
}

test('only Hobby Station product pages become cart targets', () => {
  assert.deepEqual(hobbystationTarget({product_url: URL_LO + '/'}),
    {gid: 'hs', ver: '305273', cid: '', kizu: 0, product_url: URL_LO});
  assert.equal(hobbystationTarget({product_url: 'http://www.hobbystation-single.jp/lo/product/detail/1'}), null);
  assert.equal(hobbystationTarget({product_url: 'https://evil.example/lo/product/detail/1'}), null);
  assert.equal(hobbystationTarget({product_url: 'https://yuyu-tei.jp/sell/ws/card/a/1'}), null);
  assert.equal(hobbystationTarget({}), null);
});

test('increase adds through the product URL and takes the verified cart from the reply', async () => {
  const client = fakeClient(), cart = createHobbystationCart(client, () => {}, () => 'key-1');
  await cart.refresh();
  const target = hobbystationTarget({product_url: URL_LO});
  assert.equal(cart.quantity(target), 0);
  await cart.increase(target, {product_url: URL_LO});
  assert.deepEqual(client.calls[1], ['add', {product_url: URL_LO, counter: 1}, 'key-1']);
  assert.equal(client.calls.length, 2); // no re-read: the add reply carries the cart
  assert.equal(cart.quantity(target), 1);
  assert.equal(cart.state.totalQuantity, 1);
});

test('typed quantities add the difference or set the row by product_class_id', async () => {
  const client = fakeClient([hsRow(2)]), cart = createHobbystationCart(client, () => {}, () => 'k');
  await cart.refresh();
  const target = hobbystationTarget({product_url: URL_LO});
  await cart.setTo(target, 5);
  assert.deepEqual(client.calls.at(-1), ['add', {product_url: URL_LO, counter: 3}, 'k']);
  await cart.setTo(target, 1);
  assert.deepEqual(client.calls.at(-1), ['set', '9001', 1]);
  await cart.decrease(target);
  assert.deepEqual(client.calls.at(-1), ['set', '9001', 0]);
  assert.equal(cart.quantity(target), 0);
});

test('Hobby Station errors map to their own messages; credential problems are page notices', async () => {
  assert.equal(hobbystationCartErrorKey(new Error('hobbystation_credentials_missing')), 'hs_cart_need_credentials');
  assert.equal(hobbystationCartErrorKey({detail: {type: 'hobbystation_product_access_check'}}), 'hs_cart_access_check');
  assert.equal(hobbystationCartErrorKey({detail: {type: 'hobbystation_add_form_unavailable'}}), 'hs_cart_not_addable');
  assert.equal(hobbystationCartErrorKey({detail: {type: 'hobbystation_stock_exceeded', outcome: 'unknown'}}), 'cart_limit_reached');
  assert.equal(hobbystationCartErrorKey({detail: {type: 'hobbystation_unavailable', outcome: 'unknown'}}), 'cart_unverified');
  assert.equal(hobbystationCartErrorKey({detail: {type: 'other'}}), 'cart_failed');
  for (const key of ['hs_cart_need_credentials', 'hs_cart_credentials_rejected', 'hs_cart_access_check'])
    assert.equal(messageTier(key), 'account');

  const client = fakeClient();
  client.add = async () => { const e = new Error('x'); e.status = 409; e.detail = {type: 'hobbystation_credentials_rejected'}; throw e; };
  const cart = createHobbystationCart(client, () => {}, () => 'k');
  await cart.refresh();
  await cart.increase(hobbystationTarget({product_url: URL_LO}));
  assert.equal(cart.state.notice, 'hs_cart_credentials_rejected');
});

test('load replaces the cart with one read elsewhere', async () => {
  const cart = createHobbystationCart(fakeClient(), () => {});
  const {hobbystationCartRows} = await import(asModule((await read('hs-cart.js')).replace("'./cart.js'", JSON.stringify(cartModule))));
  cart.load(hobbystationCartRows(cartOf([hsRow(4)])));
  assert.equal(cart.state.configured, true);
  assert.equal(cart.quantity(hobbystationTarget({product_url: URL_LO})), 4);
});

test('a rejected increase shows the shop reason on the affected card and reloads its quantity', async () => {
  const target = hobbystationTarget({product_url: URL_LO});
  for (const [reason, key] of [['purchase_limit', 'hs_cart_purchase_limit'], ['stock', 'hs_cart_stock_exceeded']]) {
    const client = fakeClient([hsRow(4)]);
    client.add = async () => {
      throw Object.assign(new Error('hobbystation_request_failed'), {status: 409,
        detail: {type: 'hobbystation_stock_exceeded', reason, outcome: 'unknown'}});
    };
    const cart = createHobbystationCart(client, () => {}, () => 'key');
    await cart.refresh();
    if (reason === 'purchase_limit') await cart.increase(target);
    else await cart.setTo(target, 6);
    assert.deepEqual(client.calls, [['query'], ['query']]);
    assert.equal(cart.message(target), key);
    assert.equal(messageTier(key), 'flash');
    assert.equal(cart.state.notice, '');
    assert.equal(cart.quantity(target), 4);
  }
});
