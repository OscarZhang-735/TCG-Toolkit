import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = (await readFile(new URL('../src/tcg_toolkit/static/catalog/product-details.js', import.meta.url), 'utf8'))
  .replace("import {productKey} from './product-identity.js';", 'const productKey = t => JSON.stringify([t.gid, t.ver, t.cid, t.kizu]);');
const {createProductDetails} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const target = (cid, kizu = 0) => ({gid: 56, ver: 'OS01', cid, kizu});
const tick = () => new Promise(r => setTimeout(r, 0));

test('known search details need no request; unknown products are fetched once', async () => {
  const urls = [];
  let changes = 0;
  const details = createProductDetails(() => changes++);
  details.activate(async url => { urls.push(url); return new Response(JSON.stringify({card_number: 'OS01/R01-001', rarity: 'SP'})); });
  details.remember(target('1'), {card_number: 'A-1', rarity: 'R'});
  assert.deepEqual(details.get(target('1', 1)), {status: 'done', card_number: 'A-1', rarity: 'R'}, 'Damaged copy shares print details');
  assert.equal(details.get(target('2')).status, 'loading');
  details.get(target('2'));
  await tick(); await tick();
  assert.deepEqual(urls, ['/api/yuyutei/product?gid=56&ver=OS01&cid=2']);
  assert.equal(details.get(target('2')).card_number, 'OS01/R01-001');
  assert.equal(changes, 1);
});

test('at most three lookups run at once; failures retry only when asked', async () => {
  let running = 0, peak = 0, fail = true;
  const details = createProductDetails(() => {}, 3);
  details.activate(async () => {
    running++; peak = Math.max(peak, running); await tick(); running--;
    return fail ? new Response('{}', {status: 502}) : new Response(JSON.stringify({card_number: 'N', rarity: null}));
  });
  for (let i = 0; i < 7; i++) details.get(target(String(i)));
  for (let i = 0; i < 20; i++) await tick();
  assert.equal(peak, 3);
  assert.equal(details.get(target('0')).status, 'failed');
  fail = false; details.retryFailed();
  details.get(target('0')); for (let i = 0; i < 5; i++) await tick();
  assert.equal(details.get(target('0')).card_number, 'N');
});

test('signing out drops pending replies', async () => {
  let resolve;
  const details = createProductDetails();
  details.activate(() => new Promise(r => { resolve = r; }));
  details.get(target('1'));
  details.clear();
  resolve(new Response(JSON.stringify({card_number: 'X', rarity: null}))); await tick(); await tick();
  details.activate(async () => new Response('{}', {status: 502}));
  assert.equal(details.get(target('1')).status, 'loading');
});
