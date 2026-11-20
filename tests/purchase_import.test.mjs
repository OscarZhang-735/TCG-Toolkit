import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const read = path => readFile(new URL('../src/tcg_toolkit/static/' + path, import.meta.url), 'utf8');
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const source = (await read('collection/purchase-import.js'))
  .replace("'../catalog/card-sources.js'", JSON.stringify(moduleUrl(await read('catalog/card-sources.js'))))
  .replace("'./yuyutei-order.js'", JSON.stringify(moduleUrl(await read('collection/yuyutei-order.js'))));
const {createPurchaseImporter} = await import(moduleUrl(source));
const ok = body => ({ok: true, json: async () => body});
const receipt = (games, ...lines) => ({source: 'yuyutei', games, text: lines.join('\n')});
const printing = (game, card_number, rarity, name) => ({game, card_number, rarity, name, image_url: 'https://example.org/card.jpg'});

test('every distinct number queries every chosen catalog, regardless of its format', async () => {
  const rose = printing('WSR', '0042', 'RR', '強がるオトナのSecret Labo 茉優');
  const frame = {...rose, card_number: 'OS01/R01-026OFR', rarity: 'OFR'};
  const ws = printing('WS', 'OS01/R01-026', 'R', '別作品');
  const data = {wsr: [frame, rose], ws: [ws]};
  const requests = [];
  const importer = createPurchaseImporter({request: async url => {
    requests.push(url);
    return ok({items: data[url.split('/')[3]]});
  }});
  const {items} = await importer.preview(receipt(['WS', 'WSR', 'WSR'],
    '0042 RR 茉優 180円 数量:6', 'OS01/R01-026OFR OFR 茉優 680円 数量:4',
    'OS01/R01-026 R 別作品 180円 数量:2', '0042 RR 茉優 180円 数量:1', '0042 SR 茉優 180円 数量:1',
  ));
  const expected = ['0042', 'OS01/R01-026OFR', 'OS01/R01-026'].flatMap(word =>
    ['ws', 'wsr'].map(game => '/api/official/' + game + '/search?word=' + encodeURIComponent(word) + '&exact=true'));
  assert.deepEqual(requests.sort(), expected.sort());
  assert.deepEqual(items.map(row => row.quantity), [6, 4, 2, 1, 1]);
  assert.deepEqual(items.map(row => row.matches), [[rose], [frame], [ws], [rose], []]);
  assert.deepEqual(items.map(row => row.status), ['matched', 'matched', 'matched', 'matched', 'missing']);
});

test('multiple records fail within one catalog or across catalogs even when a receipt name agrees', async () => {
  const sameNumber = ['カードA', 'カードB'].map(name => printing('PM', '01-001', 'R', name));
  const importer = createPurchaseImporter({request: async url => {
    const {pathname, searchParams} = new URL(url, 'https://test.example');
    const game = pathname.includes('/pm/') ? 'PM' : 'WS';
    const number = searchParams.get('word');
    if (number === '01-001') return ok({items: game === 'PM' ? sameNumber : []});
    return ok({items: [printing(game, number, 'R', game)]});
  }});
  const {items} = await importer.preview(receipt(['WS', 'PM'],
    '01-001 R カードB 180円 数量:1', 'LO-1606-S R PM 180円 数量:1',
  ));
  assert.deepEqual(items.map(row => row.status), ['ambiguous', 'ambiguous']);
  assert.deepEqual(items.map(row => row.matches), [[], []]);
});

test('a partial lookup cannot establish uniqueness, and each other line keeps its outcome', async () => {
  const card = printing('SEV EN', 'BP01-001', 'R', 'English card');
  const urls = [];
  const importer = createPurchaseImporter({request: async url => {
    urls.push(url);
    if (url.includes('BP01-001') && url.includes('/sev/')) return {ok: false, status: 504};
    return ok({items: url.includes('/sev_en/') ? [card] : []});
  }});
  const {items} = await importer.preview(receipt(['SEV EN', 'SEV'],
    'BP01-001 R English card 10円 数量:1', 'BP01-002 R Missing 10円 数量:1', '01-002 R Missing quantity',
  ));
  assert.equal(urls.length, 4);
  assert.deepEqual(items.map(row => row.status), ['failed', 'missing', 'invalid']);
  assert.deepEqual(items.map(row => row.matches), [[], [], []]);
});

test('preview requires at least one supported official game before querying', async () => {
  const importer = createPurchaseImporter({request: () => assert.fail('Unexpected request')});
  for (const games of [undefined, [], ['ChaosTCG'], ['WS', 'Other']]) {
    await assert.rejects(importer.preview(receipt(games, '01-001 R Card 10円 数量:1')), /invalid_purchase_games/);
  }
});

test('confirm sends the reviewed quantities without setting valuations, reusing the confirmation id on retry', async () => {
  let loads = 0;
  const requests = [], result = {created: 0, updated: 1, quantity: 3};
  const importer = createPurchaseImporter({inventory: {load: async () => { loads++; }}, request: async (url, options) => {
    requests.push({url, options});
    if (requests.length === 1) return {ok: false, status: 503, json: async () => ({detail: 'server_unavailable'})};
    return ok(result);
  }});
  const card = printing('WS', 'BD/WE49-56SP', 'SP', '公式名'), signal = new AbortController().signal;
  const input = {source: 'yuyutei', importId: '76e4cee8-a6dd-4dd5-ba0a-f0bd18e5c3be', groupId: 'g',
    items: [{...card, quantity: 3, price: 2500, currency: 'JPY', line: 4}]};
  await assert.rejects(importer.confirm(input, signal), {status: 503, detail: 'server_unavailable'});
  assert.deepEqual(await importer.confirm(input, signal), result);
  assert.equal(loads, 1);
  assert.deepEqual(requests[0], requests[1]);
  const {url, options} = requests[1];
  assert.equal(url, '/api/cards/purchases');
  assert.equal(options.method, 'POST');
  assert.equal(options.headers['Idempotency-Key'], input.importId);
  assert.equal(options.signal, signal);
  assert.deepEqual(JSON.parse(options.body), {group_id: 'g', items: [{...card, quantity: 3}]});
});

test('committed import remains successful when refreshing the library fails', async () => {
  const result = {created: 1, updated: 0, quantity: 2};
  const importer = createPurchaseImporter({request: async () => ok(result),
    inventory: {load: async () => { throw new Error('read failed'); }}});
  const old = console.error;
  console.error = () => {};
  try {
    assert.deepEqual(await importer.confirm({source: 'yuyutei', importId: 'example',
      items: [{...printing('WS', 'BD/WE49-56SP', 'SP', 'Card'), quantity: 2}]}), result);
  } finally { console.error = old; }
});
