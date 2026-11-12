import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const source = await readFile(new URL('../src/tcg_toolkit/static/cards.js', import.meta.url), 'utf8');
const {createCards, filterCards, summarizeCards, yenPrice, priceGame, rankOffers, pickOffer, fetchPriceMerchants, lookupPrices} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
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
test('price bounds are inclusive and leave out unpriced cards only while a bound is set', () => {
  const rows = [{...card, id: 'free', price: 0}, {...card, id: 'mid', price: 12.5}, {...card, id: 'top', price: 99.99},
    {...card, id: 'unset', price: null}, {...card, id: 'old'}];
  const ids = bounds => filterCards(rows, bounds).map(row => row.id);
  assert.deepEqual(ids({}), ['free', 'mid', 'top', 'unset', 'old']);
  assert.deepEqual(ids({min: 0}), ['free', 'mid', 'top']);
  assert.deepEqual(ids({min: 12.5, max: 99.99}), ['mid', 'top']);
  assert.deepEqual(ids({max: 12.5}), ['free', 'mid']);
  assert.deepEqual(ids({min: 13, max: 50}), []);
});
test('prices in other currencies compare by yen value; unknown rates stay out of bounds', () => {
  const rates = {JPY: 1, CNY: 0.05};
  assert.equal(yenPrice({price: 800}, rates), 800);
  assert.equal(yenPrice({price: 800, currency: 'JPY'}, {}), 800);
  assert.equal(yenPrice({price: 35, currency: 'CNY'}, rates), 700);
  assert.equal(yenPrice({price: 10, currency: 'CAD'}, rates), null);
  assert.equal(yenPrice({price: null, currency: 'CNY'}, rates), null);
  const rows = [{...card, id: 'yen', price: 800, currency: 'JPY'}, {...card, id: 'yuan', price: 35, currency: 'CNY'},
    {...card, id: 'cad', price: 10, currency: 'CAD'}];
  const ids = bounds => filterCards(rows, {...bounds, priceOf: row => yenPrice(row, rates)}).map(row => row.id);
  assert.deepEqual(ids({}), ['yen', 'yuan', 'cad']);
  assert.deepEqual(ids({min: 750}), ['yen']);
  assert.deepEqual(ids({max: 700}), ['yuan']);
});
test('drafts carry the currency, defaulting to JPY', async () => {
  const sent = [];
  const cards = createCards(() => {});
  await cards.activate(async (url, options) => {
    if (options.method === 'GET') return reply({items: url.endsWith('/groups') ? [] : [{...card, price: 35, currency: 'CNY'}]});
    const body = JSON.parse(options.body); sent.push(body); return reply({...card, ...body}, 201);
  });
  cards.edit('one'); assert.equal(cards.state.draft.currency, 'CNY');
  cards.state.draft.quantity = 4; await cards.save(); assert.equal(sent.at(-1).currency, 'CNY');
  cards.edit(); assert.equal(cards.state.draft.currency, 'JPY');
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
    return reply({...(cards.find(row => row.id === pathname.split('/')[3]) ?? cards[0]), ...body});
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
  // Editing starts from the card's own group and saves the choice with the card fields.
  cards.edit('one'); assert.equal(cards.state.draftGroup, 'g1');
  cards.state.draft.quantity = 3; cards.state.draftGroup = 'g2'; await cards.save();
  assert.deepEqual([server.calls.at(-1).method, server.calls.at(-1).body.group_id], ['PUT', 'g2']);
  assert.equal(cards.state.records.find(row => row.id === 'one').group_id, 'g2');
  assert.equal(cards.state.draftGroup, 'g1');
  cards.edit('two'); assert.equal(cards.state.draftGroup, null);
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

test('price is edited as text, sent as an exact decimal string, and blank clears it', async () => {
  const saved = {...card, price: 12.5};
  const sent = [];
  const cards = createCards(() => {});
  await cards.activate(async (url, options) => {
    if (options.method === 'GET') return reply({items: url.endsWith('/groups') ? [] : [saved, {...card, id: 'two', price: null}]});
    const body = JSON.parse(options.body); sent.push(body);
    return reply({...saved, ...body, price: body.price === null ? null : Number(body.price)}, options.method === 'POST' ? 201 : 200);
  });
  cards.edit('one'); assert.equal(cards.state.draft.price, '12.5');
  cards.edit('two'); assert.equal(cards.state.draft.price, '');
  // A quantity-only save keeps the existing price.
  cards.edit('one'); cards.state.draft.quantity = 5; await cards.save();
  assert.equal(sent.at(-1).price, '12.5');
  cards.edit('one'); cards.state.draft.price = ' 0.10 '; await cards.save();
  assert.equal(sent.at(-1).price, '0.10'); assert.equal(cards.state.records[0].price, 0.1);
  cards.edit('one'); cards.state.draft.price = ''; await cards.save();
  assert.equal(sent.at(-1).price, null); assert.equal(cards.state.records[0].price, null);
  cards.edit(); assert.equal(cards.state.draft.price, '');
  Object.assign(cards.state.draft, {game: 'WS', card_number: 'NEW-1', rarity: 'R', name: 'New', price: '1234.56'});
  await cards.save(); assert.equal(sent.at(-1).price, '1234.56'); assert.equal(sent.at(-1).group_id, null);
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

test('price lookup maps library games to merchant ids and ranks the draft rarity first', () => {
  assert.deepEqual(['WS', 'WS EN', 'WSR', 'PM', 'SEV', 'SEV EN', 'Lycee', 'ChaosTCG', ' chaostcg ', 'Custom Game', ''].map(priceGame),
    ['ws', 'ws_en', 'wsr', 'pm', 'sev', 'sev_en', 'lycee', 'chaos', 'chaos', 'custom_game', '']);
  const offers = [{product_url: 'a', rarity: 'R'}, {product_url: 'b', rarity: 'sr'}, {product_url: 'c', rarity: null}, {product_url: 'd', rarity: 'SR'}];
  assert.deepEqual(rankOffers(offers, ' SR ').map(offer => offer.product_url), ['b', 'd', 'a', 'c']);
  assert.deepEqual(rankOffers(offers, '').map(offer => offer.product_url), ['a', 'b', 'c', 'd']);
  assert.notEqual(rankOffers(offers, 'R'), offers);
});

test('price lookup requests YuYu-Tei and turns failures into translation keys', async () => {
  const calls = [];
  const api = responses => async (url, options) => { calls.push({url, options}); return responses.shift(); };
  const merchants = await fetchPriceMerchants(api([reply({items: [{id: 'yuyutei', name: '游游亭', games: ['ws', 'lycee']}]})]));
  assert.deepEqual(merchants.map(({id, name, games}) => [id, name, [...games]]), [['yuyutei', '游游亭', ['ws', 'lycee']]]);
  assert.equal(calls.at(-1).url, '/api/prices/merchants');
  await assert.rejects(fetchPriceMerchants(api([reply({detail: 'x'}, 500)])));
  const offer = {name: 'Card', rarity: 'SR', price: 100, currency: 'JPY', product_url: 'https://yuyu-tei.jp/x'};
  assert.deepEqual(await lookupPrices(api([reply({items: [offer]})]), {game: 'ChaosTCG', card_number: ' AB/C-001 '}), [offer]);
  const url = new URL(calls.at(-1).url, 'http://local');
  assert.equal(url.pathname, '/api/prices');
  assert.deepEqual(Object.fromEntries(url.searchParams), {merchant: 'yuyutei', game: 'chaos', card_number: 'AB/C-001'});
  assert.equal(calls.at(-1).options.cache, 'no-store');
  for (const [status, detail, key] of [[504, 'price_source_timeout', 'price_lookup_timeout'], [502, 'price_source_unavailable', 'price_lookup_unavailable'],
    [502, 'price_source_unreadable', 'price_lookup_unreadable'], [422, 'unsupported_merchant_game', 'price_lookup_unsupported'],
    [422, [{msg: 'bad'}], 'price_lookup_failed'], [500, undefined, 'price_lookup_failed']]) {
    await assert.rejects(lookupPrices(api([reply({detail}, status)]), {game: 'WS', card_number: 'A'}), error => error.key === key);
  }
  const broken = {ok: false, status: 502, json: async () => { throw new SyntaxError('not json'); }};
  await assert.rejects(lookupPrices(api([broken]), {game: 'WS', card_number: 'A'}), error => error.key === 'price_lookup_failed');
});

test('bulk lookup picks the cheapest same-rarity offer, preferring stock', () => {
  const offer = (price, rarity, stock_status = 'in_stock', currency = 'JPY') => ({price, rarity, stock_status, currency, product_url: `${rarity}-${price}`});
  const offers = [offer(300, 'SR'), offer(120, 'sr', 'out_of_stock'), offer(200, 'SR'), offer(50, 'RR'), offer(10, 'SR', 'in_stock', 'USD')];
  assert.equal(pickOffer(offers, ' sr ').product_url, 'SR-200');
  // Without any in stock, the cheapest of the rest.
  assert.equal(pickOffer([offer(300, 'SR', 'unknown'), offer(120, 'SR', 'out_of_stock')], 'SR').product_url, 'SR-120');
  assert.equal(pickOffer([offer(0, 'SR', 'out_of_stock')], 'SR').price, 0);
  assert.equal(pickOffer(offers, 'SP'), null);
  assert.equal(pickOffer(offers, ''), null);
  assert.equal(pickOffer([{...offer(5, null)}], 'SR'), null);
  assert.equal(pickOffer([], 'SR'), null);
});

test('lookups can name another merchant; setPrice saves only price and currency', async () => {
  const urls = [];
  const lookupApi = async url => { urls.push(url); return reply({items: []}); };
  await lookupPrices(lookupApi, {game: 'WS', card_number: 'A'}, undefined, 'other');
  assert.equal(new URL(urls[0], 'http://local').searchParams.get('merchant'), 'other');
  const priced = {...card, group_id: 'g1', price: 12.5, currency: 'CNY', image_url: 'https://img/x.png'};
  const server = groupServer({cards: [priced], groups: decks}), cards = createCards(() => {});
  await cards.activate(server.api);
  assert.equal(await cards.setPrice('one', 1280, 'JPY'), '');
  const sent = server.calls.at(-1);
  assert.deepEqual([sent.method, sent.path], ['PUT', '/api/cards/one']);
  assert.deepEqual(sent.body, {game: 'WS', card_number: 'ABC-001', rarity: 'SR', name: 'First', quantity: 2, price: '1280.00',
    currency: 'JPY', notes: 'Keep', image_url: 'https://img/x.png'});
  assert.equal('group_id' in sent.body, false);
  assert.deepEqual([cards.state.records[0].price, cards.state.records[0].currency], ['1280.00', 'JPY']);
  assert.equal(cards.state.busy, false);
  assert.equal(await cards.setPrice('missing', 1, 'JPY'), 'cards_failed');
  server.failNext(404, 'card_not_found');
  assert.equal(await cards.setPrice('one', 1, 'JPY'), 'cards_missing');
});
