import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';
const source = await readFile(new URL('../src/tcg_toolkit/static/catalog/card-sources.js', import.meta.url), 'utf8');
const {lookupSource, lookupItems} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
test('ChaosTCG uses the existing authenticated marketplace search', () => {
  const config = lookupSource('ChaosTCG', 'AB/001');
  assert.equal(config.url, '/api/search?game=chaos&page=1&keyword=AB%2F001');
  assert.equal(config.marketplace, true);
  const items = lookupItems(config, [{game: 'chaos', card_number: 'AB/001', name: 'Card', rarity: null, stock_quantity: 9, price: 100}]);
  assert.equal(items[0].game, 'ChaosTCG');
  assert.equal(items[0].is_official, false);
  assert.equal(items[0].source, 'yuyutei');
  assert.equal(items[0].rarity, '');
  assert.equal('quantity' in items[0], false);
  assert.equal('price' in items[0], false);
});
test('official games keep their sources; other games have no lookup', () => {
  for (const game of ['WS', 'WSR', 'Lycee', 'PM']) {
    const config = lookupSource(game, 'test');
    assert.equal(config.marketplace, false);
    assert.equal(config.url, `/api/official/${game.toLowerCase()}/search?word=test`);
    const items = [{game}]; assert.equal(lookupItems(config, items), items);
  }

  assert.equal(lookupSource('Unknown', 'test'), null);
});

test('SEV inventory uses Japanese official data', () => {
  const config = lookupSource('SEV', 'CP04-021');
  assert.equal(config.url, '/api/official/sev/search?word=CP04-021');
  assert.equal(config.marketplace, false);
  const items = [{game: 'SEV', rarity: 'LG'}];
  assert.equal(lookupItems(config, items), items);
});

test('SEV EN never reuses the Japanese marketplace source', () => {
  assert.equal(lookupSource('SEV EN', 'CP04-021EN').url, '/api/official/sev_en/search?word=CP04-021EN');
  assert.equal(lookupSource('sev_en', 'BP01').marketplace, false);
});

test('WS EN uses official keyword search and preserves results', () => {
  for (const game of ['WS EN', 'ws_en']) {
    const config = lookupSource(game, 'BD/WE34');
    assert.equal(config.url, '/api/official/ws_en/search?word=BD%2FWE34');
    assert.equal(config.marketplace, false);
    const items = [{game: 'WS EN', card_number: 'BD/WE34-E01', rarity: 'RR'}];
    assert.equal(lookupItems(config, items), items);
  }
});
