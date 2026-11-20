import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';
const source = await readFile(new URL('../src/tcg_toolkit/static/catalog/rarities.js', import.meta.url), 'utf8');
const {rarityTier, rarityDescription} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('badge tier follows the per-game category and defaults to normal', () => {
  assert.equal(rarityTier('wsr', 'SEC'), 'rare');
  assert.equal(rarityTier('WSR', 'pr'), 'special');
  assert.equal(rarityTier('wsr', 'PR+'), 'special');
  assert.equal(rarityTier('ws', 'C'), 'normal');
  assert.equal(rarityTier('ChaosTCG', 'SSP'), 'rare');
  assert.equal(rarityTier('lycee', 'SR'), 'normal');
  assert.equal(rarityTier('pm', 'SR'), 'normal');
  assert.equal(rarityTier('wsr', 'NOPE'), 'normal');
  assert.equal(rarityTier('wsr', null), 'normal');
});

test('hover description uses the interface language', () => {
  assert.equal(rarityDescription('wsr', 'SEC', 'en'), 'SEC · Secret');
  assert.equal(rarityDescription('wsr', 'sec', 'zh-CN'), 'SEC · 秘密稀有卡');
  assert.equal(rarityDescription('pm', 'SR', 'en'), null);
  assert.equal(rarityDescription('sev', 'SR', 'en'), 'SR · Silver Rare');
  assert.equal(rarityDescription('SEV EN', 'S', 'zh-CN'), 'S · 银稀有卡');
  assert.equal(rarityTier('sev_en', 'U'), 'rare');
  assert.equal(rarityTier('sev', 'SD'), 'special');
});
