import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const source = await readFile(new URL('../src/tcg_toolkit/static/cards.js', import.meta.url), 'utf8');
const {createCards, filterCards, summarizeCards} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const card = {id: 'one', game: 'WS', card_number: 'ABC-001', rarity: 'SR', name: 'First', quantity: 2, notes: 'Keep'};
const reply = (data, status = 200) => ({ok: status < 400, status, json: async () => data});

test('SEV language brands stay separate in filters and quantity statistics', () => {
  const rows = [{...card, game: 'SEV', rarity: 'SP', quantity: 2},
    {...card, id: 'english', game: 'SEV EN', rarity: 'SP', quantity: 5}];
  assert.deepEqual(filterCards(rows, {game: 'sev'}), [rows[0]]);
  assert.deepEqual(filterCards(rows, {game: 'sev en'}), [rows[1]]);
  assert.deepEqual(summarizeCards(rows).games, [
    {label: 'SEV', kinds: 1, quantity: 2}, {label: 'SEV EN', kinds: 1, quantity: 5}]);
});

test('filters and grouped statistics include zero stock without counting its type', () => {
  const rows = [card, {...card, id: 'two', game: 'Custom', name: 'Other', rarity: 'R', quantity: 0}, {...card, id: 'three', quantity: 3}];
  const stats = summarizeCards(rows);
  assert.equal(stats.kinds, 2); assert.equal(stats.quantity, 5);
  assert.deepEqual(stats.games, [{label: 'WS', kinds: 2, quantity: 5}, {label: 'Custom', kinds: 0, quantity: 0}]);
  assert.equal(filterCards(rows, {query: ' abc ', game: 'ws', rarity: 'sr'}).length, 2);
  assert.equal(filterCards(rows, {query: 'other'}).length, 1);
  assert.deepEqual(summarizeCards([]), {kinds: 0, quantity: 0, games: [], rarities: []});
});
test('duplicate and failed saves preserve draft; failed deletion preserves data', async () => {
  const cards = createCards(() => {}); let failure = 409;
  await cards.activate(async (_, options) => options.method === 'GET' ? reply({items: [card]}) : reply({detail: {id: 'one'}}, failure));
  cards.state.draft = {...card, name: 'Unsaved'};
  await cards.save();
  assert.equal(cards.state.error, 'cards_duplicate'); assert.equal(cards.state.duplicate, 'one');
  assert.equal(cards.state.draft.name, 'Unsaved');
  cards.edit(cards.state.duplicate); assert.equal(cards.state.editing, 'one'); assert.equal(cards.state.draft.name, 'First');
  failure = 500; cards.state.draft.notes = 'Draft'; await cards.save();
  assert.equal(cards.state.draft.notes, 'Draft'); assert.equal(cards.state.records[0].notes, 'Keep');
  await cards.remove('one'); assert.deepEqual(cards.state.records, [card]);
});
test('save updates quantity and delete updates statistics', async () => {
  const cards = createCards(() => {});
  await cards.activate(async (_, options) => options.method === 'GET' ? reply({items: [card]}) : options.method === 'DELETE' ? reply(null, 204) : reply({...card, ...JSON.parse(options.body)}));
  cards.edit('one'); cards.state.draft.quantity = 4; await cards.save();
  assert.equal(summarizeCards(cards.state.records).quantity, 4);
  assert.equal(cards.state.editing, '');
  await cards.remove('one'); assert.equal(summarizeCards(cards.state.records).quantity, 0);
});
test('old account load and mutation responses cannot repopulate state', async () => {
  const cards = createCards(() => {}); const pending = []; let resolve;
  const loading = cards.activate(() => new Promise(r => {pending.push(r);}));
  await cards.activate(async () => reply({items: []}));
  for (const late of pending) late(reply({items: [card]}));
  await loading;
  assert.deepEqual(cards.state.records, []); assert.deepEqual(cards.state.groups, []);
  await cards.activate(async (_, options) => options.method === 'GET' ? reply({items: [card]}) : new Promise(r => {resolve = r;}));
  cards.edit('one'); const saving = cards.save(); cards.clear();
  resolve(reply(card)); await saving;
  assert.deepEqual(cards.state.records, []); assert.equal(cards.state.draft.name, ''); assert.equal(cards.state.ready, false);
});

// Fake card API with groups: GET lists, POST adds, group endpoints edit groups and membership.
function groupServer({cards = [], groups = []} = {}) {
  const calls = [];
  let fail = null;
  const api = async (url, options) => {
    const body = options.body ? JSON.parse(options.body) : null, {pathname} = new URL(url, 'http://local');
    calls.push({method: options.method, path: pathname, body});
    if (fail) { const [status, detail] = fail; fail = null; return reply({detail}, status); }
    if (pathname === '/api/cards' && options.method === 'GET') return reply({items: cards});
    if (pathname === '/api/cards/groups' && options.method === 'GET') return reply({items: groups});
    if (pathname === '/api/cards/groups' && options.method === 'POST') return reply({id: 'g-new', name: body.name, created_at: 1}, 201);
    if (pathname.startsWith('/api/cards/groups/') && options.method === 'PUT') return reply({id: pathname.split('/').pop(), name: body.name, created_at: 1});
    if (pathname.startsWith('/api/cards/groups/') && options.method === 'DELETE') return reply(null, 204);
    if (pathname.endsWith('/group')) return reply({...cards.find(row => row.id === pathname.split('/')[3]), group_id: body.group_id});
    if (pathname === '/api/cards' && options.method === 'POST') return reply({...body, id: 'new', created_at: 2, updated_at: 2}, 201);
    return reply({...cards[0], ...body});
  };
  return {api, calls, failNext: (status, detail) => { fail = [status, detail]; }};
}
const decks = [{id: 'g1', name: 'Deck', created_at: 1}, {id: 'g2', name: 'Binder', created_at: 2}];

test('groups load with cards; the selected group filters and receives new cards', async () => {
  const rows = [{...card, group_id: 'g1'}, {...card, id: 'two', card_number: 'ABC-002', group_id: null}];
  const server = groupServer({cards: rows, groups: decks}), cards = createCards(() => {});
  await cards.activate(server.api);
  assert.deepEqual(cards.state.groups, decks); assert.equal(cards.visible().length, 2);
  cards.select('g1'); assert.deepEqual(cards.visible().map(row => row.id), ['one']);
  cards.select('missing'); assert.equal(cards.state.group, 'g1');
  cards.edit(); assert.equal(cards.state.draftGroup, 'g1');
  Object.assign(cards.state.draft, {game: 'WS', card_number: 'NEW-1', rarity: 'R', name: 'New'});
  await cards.save();
  assert.equal(server.calls.at(-1).body.group_id, 'g1'); assert.equal(cards.state.records[0].group_id, 'g1');
  // Editing sends only card fields; membership changes go through the move endpoint.
  cards.edit('one'); assert.equal(cards.state.draftGroup, null);
  cards.state.draft.quantity = 3; await cards.save();
  assert.equal(server.calls.at(-1).method, 'PUT'); assert.equal('group_id' in server.calls.at(-1).body, false);
  // A choice in the add dialog overrides the selected group, and null means ungrouped.
  cards.edit(); cards.state.draftGroup = null;
  Object.assign(cards.state.draft, {game: 'WS', card_number: 'NEW-2', rarity: 'R', name: 'Loose'});
  await cards.save(); assert.equal(server.calls.at(-1).body.group_id, null);
});

test('moving a card updates it; a group deleted elsewhere reports and reloads', async () => {
  const server = groupServer({cards: [{...card, group_id: null}], groups: decks}), cards = createCards(() => {});
  await cards.activate(server.api);
  assert.equal(await cards.move('one', 'g2'), '');
  assert.deepEqual(server.calls.at(-1), {method: 'PUT', path: '/api/cards/one/group', body: {group_id: 'g2'}});
  assert.equal(cards.state.records[0].group_id, 'g2'); assert.equal(cards.state.busy, false);
  const before = server.calls.length;
  assert.equal(await cards.move('one', 'g2'), ''); assert.equal(server.calls.length, before);
  server.failNext(404, 'card_group_not_found');
  assert.equal(await cards.move('one', 'g1'), 'favorite_group_missing');
  await new Promise(resolve => setTimeout(resolve));
  assert.ok(server.calls.slice(before).some(call => call.method === 'GET' && call.path === '/api/cards/groups'));
  assert.equal(await cards.move('missing', 'g1'), 'cards_failed');
});

test('group create, rename and delete keep cards; name conflicts report a key', async () => {
  const rows = [{...card, group_id: 'g1'}];
  const server = groupServer({cards: rows, groups: decks}), cards = createCards(() => {});
  await cards.activate(server.api);
  assert.equal(await cards.createGroup('Trade'), '');
  assert.equal(cards.state.group, 'g-new'); assert.equal(cards.state.groups.at(-1).name, 'Trade');
  server.failNext(409, 'card_group_name_taken');
  assert.equal(await cards.renameGroup('g1', 'Binder'), 'favorite_group_name_taken'); assert.equal(cards.state.groupBusy, false);
  assert.equal(await cards.renameGroup('g1', 'Main deck'), '');
  assert.equal(cards.state.groups[0].name, 'Main deck');
  cards.select('g1'); cards.edit();
  assert.equal(await cards.deleteGroup('g1'), '');
  assert.deepEqual(cards.state.groups.map(group => group.id), ['g2', 'g-new']);
  assert.equal(cards.state.records[0].group_id, null); assert.equal(cards.state.records[0].quantity, card.quantity);
  assert.equal(cards.state.group, null); assert.equal(cards.state.draftGroup, null);
});

test('thumbnail survives editing and save, and resets for a new card', async () => {
  const saved = {...card, image_url: 'https://example.com/card.png'};
  let submitted;
  const cards = createCards(() => {});
  await cards.activate(async (_, options) => {
    if (options.method === 'GET') return reply({items: [saved]});
    submitted = JSON.parse(options.body); return reply({...saved, ...submitted});
  });
  cards.edit(saved.id); assert.equal(cards.state.draft.image_url, saved.image_url);
  cards.state.draft.quantity = 6; await cards.save();
  assert.equal(submitted.image_url, saved.image_url);
  assert.equal(cards.state.records[0].image_url, saved.image_url);
  assert.equal(cards.state.draft.image_url, '');
});
