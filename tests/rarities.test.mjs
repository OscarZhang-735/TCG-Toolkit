import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const source = await readFile(new URL('../src/tcg_toolkit/static/catalog/rarities.js', import.meta.url), 'utf8');
const {raritiesForGame, findRarity} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('WS keeps plus variants distinct and independent from WSR', () => {
  assert.equal(findRarity(' WS ', ' rr+ ').category, 'Normal');
  assert.equal(findRarity('ws', 'RRR+').category, 'Rare');
  assert.equal(findRarity('ws', 'SEC+').code, 'SEC+');
  assert.notEqual(findRarity('ws', 'SEC+'), findRarity('ws', 'SEC'));
  assert.equal(findRarity('wsr', 'SEC+'), null);
  assert.equal(findRarity('ws', 'TD').category, 'Special');
  assert.equal(raritiesForGame('ws').length, 33);
});

test('ChaosTCG label and API identifier share a system with game-specific meanings', () => {
  assert.equal(raritiesForGame(' ChaosTCG '), raritiesForGame('chaos'));
  assert.equal(findRarity('CHAOSTCG', 'sr').english, 'Special Rare');
  assert.equal(findRarity('chaos', 'P').english, 'Partner');
  assert.equal(findRarity('lycee', 'P').english, 'Promotional');
  assert.equal(findRarity('chaos', 'ETMSP').category, 'Rare');
  assert.equal(findRarity('chaos', 'PG').category, 'Special');
  assert.equal(findRarity('chaos', 'Unknown').category, 'Normal');
});

test('rarity lookup uses the game system and accepts casing and whitespace', () => {
  assert.equal(findRarity(' WSR ', ' sp ').category, 'Rare');
  assert.equal(findRarity('wsr', 'PR').category, 'Special');
  assert.equal(findRarity('wsr', 'R').category, 'Normal');
  assert.equal(findRarity('pm', 'SP'), null);
  assert.deepEqual(raritiesForGame('Custom'), []);
  assert.deepEqual(raritiesForGame('constructor'), []);
});

test('explicit no-rarity is distinct from missing and unrecognized values', () => {
  assert.equal(findRarity('WSR', 'Unknown').english, 'None');
  for (const value of [null, undefined, '', ' ', 'unrecognized']) {
    assert.equal(findRarity('WSR', value), null);
  }
});

test('Lycee uses its own categories for shared and exclusive codes', () => {
  assert.equal(findRarity(' Lycee ', ' sr ').category, 'Normal');
  assert.equal(findRarity('WSR', 'SR').category, 'Rare');
  assert.equal(findRarity('lycee', 'KSR').category, 'Rare');
  assert.equal(findRarity('lycee', 'P').category, 'Special');
  assert.equal(findRarity('lycee', 'PR'), null);
  assert.equal(findRarity('lycee', 'Unknown').english, 'None');
});

test('all systems use the ASCII Unknown code with the Chinese display name', () => {
  for (const game of ['ws', 'wsr', 'lycee', 'chaos', 'sev', 'sev_en']) {
    assert.equal(findRarity(game, ' unknown ').code, 'Unknown');
    assert.equal(findRarity(game, 'Unknown').chinese, '无');
    assert.equal(findRarity(game, '无'), null);
  }
});

test('SEV and SEV EN have independent language-specific rarity systems', () => {
  const normal = game => raritiesForGame(game).filter(entry => entry.category === 'Normal' && entry.order > 0);
  assert.deepEqual(normal('SEV').map(entry => entry.code), ['BR', 'SR', 'GR', 'LG']);
  assert.deepEqual(normal('SEV EN').map(entry => entry.code), ['B', 'S', 'G', 'L']);
  assert.deepEqual(normal('sev_en').map(entry => entry.order), [1, 2, 3, 4]);
  assert.equal(raritiesForGame('SEV EN'), raritiesForGame('sev_en'));
  for (const code of ['B', 'S', 'G', 'L', 'U']) assert.equal(findRarity('sev', code), null);
  for (const code of ['BR', 'SR', 'GR', 'LG', 'UR']) assert.equal(findRarity('sev_en', code), null);
  assert.equal(findRarity('sev', 'SR').english, 'Silver Rare');
  assert.equal(findRarity('sev_en', 'U').english, 'Ultimate Rare');
  assert.equal(findRarity('ws', 'U').english, 'Uncommon');
});

test('SEV maps parallel rarities and special card types independently', () => {
  for (const code of ['Premium', 'P', 'SL', 'UR', 'SP', 'SSP', 'GBF']) {
    assert.equal(findRarity('sev', code).category, 'Rare');
  }
  for (const code of ['PR', 'SD', 'LD', 'Leader', 'Token', 'EP', 'SEP', 'Serial', 'Collaboration']) {
    assert.equal(findRarity('sev', code).category, 'Special');
  }
  assert.equal(findRarity('sev_en', 'U').english, 'Ultimate Rare');
  assert.equal(findRarity('ws', 'U').english, 'Uncommon');
});

test('WS EN has its own rarity mapping including English exclusives', () => {
  assert.equal(raritiesForGame('WS EN'), raritiesForGame('ws_en'));
  assert.deepEqual(raritiesForGame('WS EN').filter(x => x.category === 'Normal').map(x => x.code), ['Unknown', 'C', 'U', 'R', 'RR', 'CC', 'CR']);
  for (const code of ['SR', 'RRR', 'RRR+', 'OFR', 'SP', 'SSP', 'SEC', 'SEC+', 'SIR', 'ENX']) assert.equal(findRarity('WS EN', code).category, 'Rare');
  for (const code of ['TD', 'PR', 'TD+']) assert.equal(findRarity('WS EN', code).category, 'Special');
  assert.equal(findRarity('ws', 'ENX'), null);
  assert.equal(findRarity('ws_en', 'AGR'), null);
});
