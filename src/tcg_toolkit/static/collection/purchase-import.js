import {lookupSource} from '../catalog/card-sources.js';
import {parseYuyuteiOrder} from './yuyutei-order.js';

const fold = text => (text ?? '').normalize('NFKC').trim().toUpperCase();

export const PURCHASE_GAMES = ['WSR', 'WS', 'WS EN', 'Lycee', 'PM', 'SEV', 'SEV EN'];

function matchPrinting(row, lookup) {
  if (lookup.failed) return {...row, matches: [], status: 'failed'};
  const matches = lookup.cards.filter(card => fold(card.rarity) === fold(row.rarity));
  if (matches.length === 1) return {...row, matches, status: 'matched'};
  return {...row, matches: [], status: matches.length ? 'ambiguous' : 'missing'};
}

export function createPurchaseImporter({inventory, request}) {
  async function preview({source, text, games}, signal) {
    if (source !== 'yuyutei') throw new Error('unsupported_purchase_source');
    if (!Array.isArray(games) || !games.length || games.some(game => !PURCHASE_GAMES.includes(game)))
      throw new Error('invalid_purchase_games');
    const catalogs = new Set(games);
    const parsed = parseYuyuteiOrder(text);
    const lookups = new Map(), jobs = [];
    for (const row of parsed.items) {
      const number = fold(row.card_number);
      if (lookups.has(number)) continue;
      const lookup = {cards: [], failed: false};
      lookups.set(number, lookup);
      for (const game of catalogs) jobs.push({number: row.card_number, game, lookup});
    }
    const queue = jobs.values();
    async function worker() {
      for (const {number, game, lookup} of queue) {
        signal?.throwIfAborted();
        const {url} = lookupSource(game, number);
        try {
          const response = await request(url + '&exact=true', {cache: 'no-store', signal});
          if (!response.ok) throw new Error('purchase_lookup_failed');
          const data = await response.json();
          signal?.throwIfAborted();
          const exact = data.items.filter(card => fold(card.game) === fold(game) && fold(card.card_number) === fold(number));
          lookup.cards.push(...exact);
        } catch (error) {
          if (signal?.aborted || error.name === 'AbortError') throw error;
          lookup.failed = true;
        }
      }
    }
    await Promise.all(Array.from({length: Math.min(3, jobs.length)}, worker));
    const items = parsed.items.map(row => matchPrinting(row, lookups.get(fold(row.card_number))));
    for (const error of parsed.errors) items.push({...error, matches: [], status: 'invalid'});
    return {items: items.sort((a, b) => a.line - b.line)};
  }

  async function confirm({source, items, groupId, importId}, signal) {
    if (source !== 'yuyutei' || !importId) throw new Error('invalid_purchase_request');
    const cards = items.map(({game, card_number, rarity, name, image_url, quantity}) =>
      ({game, card_number, rarity, name, image_url: image_url || '', quantity}));
    const response = await request('/api/cards/purchases', {method: 'POST', cache: 'no-store', signal,
      headers: {'Content-Type': 'application/json', 'Idempotency-Key': importId},
      body: JSON.stringify({group_id: groupId ?? null, items: cards})});
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw Object.assign(new Error('purchase_import_failed'), {status: response.status, detail: body?.detail});
    }
    const result = await response.json();
    // The server has committed the import. A failed list refresh must not prompt a new submission.
    if (!signal?.aborted) {
      try { await inventory.load(); } catch (error) { console.error(error); }
    }
    return result;
  }
  return {preview, confirm};
}
