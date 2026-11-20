// Card number and rarity for cart rows. The cart page omits them, so they come
// from search results already seen or, failing that, one product-page lookup each.
import {cartProductUrl, productKey} from './product-identity.js';

export function createProductDetails(onChange = () => {}, concurrency = 3) {
  let request = null, generation = 0, active = 0;
  const known = new Map(), queue = [];
  const keyOf = target => productKey({...target, kizu: 0});
  function remember(target, item) {
    if (!target || !item?.card_number) return;
    const key = keyOf(target), prior = known.get(key);
    const rarity = item.rarity || prior?.rarity || null, game = item.game || prior?.game;
    const needsDetail = (target.gid === 'hs' || target.gid === 'cm') && !(rarity && game);
    known.set(key, {status: needsDetail ? prior?.status ?? 'partial' : 'done',
      card_number: item.card_number, rarity, ...(game ? {game} : {})});
  }
  async function load(target, current) {
    const key = keyOf(target);
    try {
      const response = await request(cartProductUrl(target), {cache: 'no-store'});
      const data = response.ok ? await response.json() : null;
      if (current !== generation) return;
      const prior = known.get(key);
      if (prior?.status === 'loading') {
        known.set(key, data ? {...prior, status: 'done', card_number: data.card_number || prior.card_number,
          rarity: data.rarity || prior.rarity || null, ...(data.game ? {game: data.game} : {})} : {...prior, status: 'failed'});
      }
    } catch (error) {
      if (current !== generation || error.name === 'AbortError') return;
      const prior = known.get(key);
      if (prior?.status === 'loading') known.set(key, {...prior, status: 'failed'});
    }
    onChange();
  }
  function pump() {
    while (request && active < concurrency && queue.length) {
      const current = generation;
      active++;
      load(queue.shift(), current).finally(() => { active--; pump(); });
    }
  }
  // Returns what is known now; an unknown product is queued once and reported via onChange.
  function get(target) {
    const key = keyOf(target), entry = known.get(key);
    if (entry && entry.status !== 'partial') return entry;
    const loading = {...entry, status: 'loading'};
    known.set(key, loading);
    if (request) { queue.push(target); pump(); }
    return loading;
  }
  function retryFailed() {
    for (const [key, entry] of known) if (entry.status === 'failed') known.delete(key);
  }
  function clear() { generation++; known.clear(); queue.length = 0; request = null; }
  return {get, peek: target => known.get(keyOf(target)), remember, retryFailed, clear, activate(next) { clear(); request = next; }};
}
