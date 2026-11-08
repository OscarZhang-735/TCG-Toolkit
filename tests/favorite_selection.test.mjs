import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const base = new URL('../src/tcg_toolkit/static/', import.meta.url);
const asModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const favoritesSource = await readFile(new URL('favorites.js', base), 'utf8');
const source = (await readFile(new URL('favorite-selection.js', base), 'utf8'))
  .replace("'./favorites.js'", JSON.stringify(asModule(favoritesSource)));
const {createSelection} = await import(asModule(source));
const {cardKey} = await import(asModule(favoritesSource));

const card = id => ({game: 'wsr', product_url: `https://yuyu-tei.jp/sell/wsr/card/a/${id}`, name: id});
function models({failMove = [], failRemove = []} = {}) {
  const records = ['a', 'b', 'c'].map(id => ({id, item: card(id), group_id: null}));
  const subscribed = new Set(['b']), calls = [];
  const find = item => records.find(row => cardKey(row.item) === cardKey(item));
  const favorites = {state: {records}, find,
    async move(item, groupId) {
      calls.push(['move', item.name, groupId]);
      if (failMove.includes(item.name)) return 'favorite_failed';
      find(item).group_id = groupId; return '';
    },
    async toggle(item) {
      calls.push(['unfavorite', item.name]);
      if (failRemove.includes(item.name)) return;
      records.splice(records.indexOf(find(item)), 1);
    }};
  const subscriptions = {find: item => subscribed.has(item.name),
    async remove(item) {
      calls.push(['unsubscribe', item.name]);
      subscribed.delete(item.name); return true;
    }};
  return {favorites, subscriptions, records, calls};
}

test('selection only works while active and is pruned to the shown cards', () => {
  const {favorites, subscriptions} = models();
  const selection = createSelection(favorites, subscriptions, () => {});
  selection.toggle(card('a')); assert.equal(selection.state.keys.size, 0);
  selection.setActive(true);
  selection.setAll(['a', 'b', 'c'].map(card), true); assert.equal(selection.state.keys.size, 3);
  selection.toggle(card('c')); assert.equal(selection.state.keys.size, 2);
  selection.prune([card('a')]);
  assert.deepEqual(selection.selected().map(row => row.id), ['a']);
  selection.setActive(false); assert.equal(selection.state.keys.size, 0);
});

test('bulk move runs one card at a time and keeps failures selected', async () => {
  const {favorites, subscriptions, records, calls} = models({failMove: ['b']});
  const selection = createSelection(favorites, subscriptions, () => {});
  selection.setActive(true); selection.setAll(['a', 'b'].map(card), true);
  await selection.moveTo('deck');
  assert.deepEqual(calls, [['move', 'a', 'deck'], ['move', 'b', 'deck']]);
  assert.deepEqual(records.map(row => row.group_id), ['deck', null, null]);
  assert.deepEqual([...selection.state.keys], [cardKey(card('b'))]);
  assert.equal(selection.state.failed, 1); assert.equal(selection.state.running, false);
});

test('bulk unfavorite delegates to the favorite API and keeps failed cards selected', async () => {
  const {favorites, subscriptions, records, calls} = models({failRemove: ['b']});
  const selection = createSelection(favorites, subscriptions, () => {});
  selection.setActive(true); selection.setAll(['a', 'b', 'c'].map(card), true);
  await selection.remove();
  assert.deepEqual(calls, [['unfavorite', 'a'], ['unfavorite', 'b'], ['unfavorite', 'c']]);
  assert.deepEqual(records.map(row => row.id), ['b']);
  assert.deepEqual([...selection.state.keys], [cardKey(card('b'))]);
  assert.equal(selection.state.failed, 1);
});

test('clearing mid-run stops the remaining cards', async () => {
  const {favorites, subscriptions, calls} = models();
  let selection;
  const move = favorites.move;
  favorites.move = async (item, groupId) => { const result = await move(item, groupId); selection.clear(); return result; };
  selection = createSelection(favorites, subscriptions, () => {});
  selection.setActive(true); selection.setAll(['a', 'b', 'c'].map(card), true);
  await selection.moveTo('deck');
  assert.equal(calls.length, 1);
  assert.deepEqual(selection.state, {active: false, keys: new Set(), running: false, done: 0, total: 0, failed: 0});
});

test('bulk subscribe, unsubscribe and refresh keep the selection; refresh is paced', async () => {
  const {favorites, subscriptions, calls} = models();
  const messages = new Map();
  favorites.state.messages = messages;
  favorites.refresh = async item => { calls.push(['refresh', item.name]); messages.set(cardKey(item), item.name === 'b' ? 'favorite_missing' : 'favorite_updated'); };
  subscriptions.add = async item => { calls.push(['subscribe', item.name]); return item.name !== 'c'; };
  let pauses = 0;
  const selection = createSelection(favorites, subscriptions, () => {}, async () => { pauses++; });
  selection.setActive(true); selection.setAll(['a', 'b', 'c'].map(card), true);
  await selection.refresh();
  assert.equal(pauses, 2); assert.equal(selection.state.failed, 1); assert.equal(selection.state.keys.size, 3);
  await selection.subscribe();
  assert.equal(selection.state.failed, 1); assert.equal(selection.state.keys.size, 3);
  await selection.unsubscribe();
  assert.deepEqual(calls.map(call => call.join(' ')), ['refresh a', 'refresh b', 'refresh c', 'subscribe a', 'subscribe b', 'subscribe c',
    'unsubscribe a', 'unsubscribe b', 'unsubscribe c']);
  assert.equal(pauses, 2);
});
