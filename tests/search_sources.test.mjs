import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';
const source = await readFile(new URL('../src/tcg_toolkit/static/catalog/search-sources.js', import.meta.url), 'utf8');
const {searchTargets, searchParams, isYuyutei, supports} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('unsupported game and source pairs are skipped silently', () => {
  assert.deepEqual(searchTargets(['pm', 'ws', 'lycee'], ['hobbystation', 'cardmax']), [
    {game: 'ws', source: 'hobbystation'}, {game: 'ws', source: 'cardmax'}, {game: 'lycee', source: 'hobbystation'}]);
  assert.deepEqual(searchTargets(['pm'], ['cardmax']), []);
  assert.deepEqual(searchTargets(['pm', 'sev'], ['yuyutei']), [{game: 'pm', source: 'yuyutei'}, {game: 'sev', source: 'yuyutei'}]);
  assert.equal(supports('unknown', 'ws'), false);
});

test('stock parsing is only requested from Card Max', () => {
  assert.equal(searchParams({game: 'sev', source: 'cardmax'}, 'BP22', 2, {cardmaxStock: true}).toString(),
    'source=cardmax&game=sev&keyword=BP22&page=2&stock=true');
  assert.equal(searchParams({game: 'sev', source: 'cardmax'}, 'BP22', 1).get('stock'), null);
  assert.equal(searchParams({game: 'ws', source: 'hobbystation'}, 'x', 1, {cardmaxStock: true}).get('stock'), null);
});

test('items without a source are YuYu-Tei items', () => {
  assert.equal(isYuyutei({}), true);
  assert.equal(isYuyutei({source: 'yuyutei'}), true);
  assert.equal(isYuyutei({source: 'cardmax'}), false);
});
