import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const source = await readFile(new URL('../src/tcg_toolkit/static/favorites.js', import.meta.url));
const {createFavorites, cardKey} = await import(`data:text/javascript;base64,${source.toString('base64')}`);
const item = {game: 'wsr', product_url: 'https://yuyu-tei.jp/sell/wsr/card/a'};
const row = {id: 'one', item, refreshed_at: null};
const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});

test('add/remove share state; repeated clicks cannot duplicate requests', async () => {
  let resolve, writes = 0, notifications = 0;
  const model = createFavorites(() => notifications++);
  await model.activate(async (url, options) => options.method === 'GET' ? response({items: []}) : (writes++, new Promise(r => {resolve = r;})));
  const pending = model.toggle(item);
  await model.toggle(item);
  assert.equal(writes, 1); assert.ok(model.state.busy.has(cardKey(item)));
  resolve(response(row)); await pending;
  assert.deepEqual(model.find(item), row);
  const removing = model.toggle(item); resolve(response(null, 204)); await removing;
  assert.equal(model.find(item), undefined); assert.ok(notifications > 2);
});

test('refresh all continues after failure and retains failed snapshot', async () => {
  const other = {id: 'two', item: {...item, product_url: item.product_url + '2'}};
  let active = 0, pauses = 0;
  const model = createFavorites(() => {}, async () => {pauses++;});
  await model.activate(async (url, options) => {
    if (options.method === 'GET') return response({items: [row, other]});
    assert.equal(active++, 0); await Promise.resolve(); active--;
    return url.includes('/one/') ? response({detail: 'refresh_product_missing'}, 404) : response({...other, refreshed_at: 123});
  });
  await model.refreshAll();
  assert.deepEqual(model.state.records[0], row);
  assert.equal(model.state.records[1].refreshed_at, 123);
  assert.equal(model.state.messages.get(cardKey(item)), 'favorite_missing');
  assert.equal(model.state.done, 2); assert.equal(pauses, 1); assert.equal(model.state.running, false);
});

test('old response bodies cannot restore previous account data', async () => {
  const resolvers = [];
  const model = createFavorites(() => {});
  const pending = model.activate(async () => ({ok: true, status: 200, json: () => new Promise(r => {resolvers.push(r);})}));
  await Promise.resolve();
  await model.activate(async () => response({items: []}));
  resolvers.forEach(resolve => resolve({items: [row]})); await pending;
  assert.deepEqual(model.state.records, []);
});

test('session expiry clears refresh queue and ignores in-flight result', async () => {
  let writes = 0;
  const model = createFavorites(() => {});
  await model.activate(async (url, options) => {
    if (options.method === 'GET') return response({items: [row, {...row, id: 'two'}]});
    writes++; model.clear(); throw new DOMException('Expired', 'AbortError');
  });
  await model.refreshAll();
  assert.equal(writes, 1); assert.deepEqual(model.state.records, []); assert.equal(model.state.running, false);
});

const groups = [{id: 'deck', name: 'Deck'}, {id: 'trade', name: 'Trade'}];
const grouped = [{...row, group_id: null}, {id: 'two', item: {...item, product_url: item.product_url + '2'}, group_id: 'deck'}];
const groupedAPI = handler => async (url, options) => {
  if (options.method === 'GET') return response({items: url.endsWith('/groups') ? groups.map(g => ({...g})) : grouped.map(r => ({...r}))});
  return handler(url, options);
};

test('loads groups, filters the visible group and refreshes only that group', async () => {
  const refreshed = [];
  const model = createFavorites(() => {}, async () => {});
  await model.activate(groupedAPI(async url => {refreshed.push(url); return response({...grouped[1], refreshed_at: 1});}));
  assert.deepEqual(model.state.groups.map(g => g.id), ['deck', 'trade']);
  assert.equal(model.visible().length, 2);
  model.select('deck'); assert.deepEqual(model.visible().map(r => r.id), ['two']);
  model.select('missing'); assert.equal(model.state.group, 'deck');
  await model.refreshAll();
  assert.deepEqual(refreshed, ['/api/favorites/two/refresh']);
});

test('move, create, rename and delete keep local state in step with the server', async () => {
  const calls = [];
  const model = createFavorites(() => {});
  await model.activate(groupedAPI(async (url, options) => {
    calls.push([options.method, url, options.body && JSON.parse(options.body)]);
    if (url.endsWith('/group')) return response({...grouped[0], group_id: JSON.parse(options.body).group_id});
    if (options.method === 'POST') return response({id: 'new', name: 'New', is_default: false}, 201);
    if (options.method === 'PUT') return response({id: 'deck', name: 'Renamed', is_default: false});
    return response(null, 204);
  }));
  assert.equal(await model.move(item, 'deck'), '');
  assert.equal(model.find(item).group_id, 'deck');
  assert.equal(await model.move(item, null), '');
  assert.equal(model.find(item).group_id, null);
  assert.equal(await model.move(item, 'deck'), '');
  assert.equal(await model.createGroup('New'), '');
  assert.equal(model.state.group, 'new'); assert.equal(model.state.groups.at(-1).id, 'new');
  assert.equal(await model.renameGroup('deck', 'Renamed'), '');
  assert.equal(model.state.groups[0].name, 'Renamed');
  model.select('deck');
  assert.equal(await model.deleteGroup('deck'), '');
  assert.equal(model.state.group, null);
  assert.deepEqual(model.state.groups.map(g => g.id), ['trade', 'new']);
  assert.deepEqual(model.state.records.map(r => r.group_id), [null, null]);
  assert.deepEqual(calls.map(c => c.slice(0, 2)), [['PUT', '/api/favorites/one/group'], ['PUT', '/api/favorites/one/group'],
    ['PUT', '/api/favorites/one/group'], ['POST', '/api/favorites/groups'],
    ['PUT', '/api/favorites/groups/deck'], ['DELETE', '/api/favorites/groups/deck']]);
  assert.deepEqual(calls.slice(0, 3).map(c => c[2]), [{group_id: 'deck'}, {group_id: null}, {group_id: 'deck'}]);
});

test('group errors come back as translation keys; a missing group reloads', async () => {
  let gets = 0;
  const model = createFavorites(() => {});
  await model.activate(async (url, options) => {
    if (options.method === 'GET') {gets++; return response({items: url.endsWith('/groups') ? groups : grouped});}
    return url.endsWith('/group') ? response({detail: 'favorite_group_not_found'}, 404) : response({detail: 'favorite_group_name_taken'}, 409);
  });
  assert.equal(await model.createGroup('Deck'), 'favorite_group_name_taken');
  assert.equal(model.state.groupBusy, false); assert.equal(model.state.groups.length, 2);
  assert.equal(await model.move(item, 'deck'), 'favorite_group_missing');
  assert.equal(gets, 4); assert.equal(model.state.busy.size, 0);
});
