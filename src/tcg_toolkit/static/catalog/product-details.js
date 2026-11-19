// Card number and rarity for cart rows. The cart page omits them, so they come
// from search results already seen or, failing that, one product-page lookup each.
import {productKey} from './product-identity.js';

export function createProductDetails(onChange = () => {}, concurrency = 3) {
  let request = null, generation = 0, active = 0;
  const known = new Map(), queue = [];
  const keyOf = target => productKey({...target, kizu: 0});
  function remember(target, item) {
    if (target && item?.card_number) known.set(keyOf(target), {status: 'done', card_number: item.card_number, rarity: item.rarity ?? null});
  }
  async function load(target, current) {
    const key = keyOf(target);
    try {
      const params = new URLSearchParams({gid: target.gid, ver: target.ver, cid: target.cid});
      const response = await request(`/api/yuyutei/product?${params}`, {cache: 'no-store'});
      const data = response.ok ? await response.json() : null;
      if (current !== generation) return;
      known.set(key, data ? {status: 'done', card_number: data.card_number, rarity: data.rarity} : {status: 'failed'});
    } catch (error) {
      if (current !== generation || error.name === 'AbortError') return;
      known.set(key, {status: 'failed'});
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
    if (entry) return entry;
    const loading = {status: 'loading'};
    known.set(key, loading);
    if (request) { queue.push(target); pump(); }
    return loading;
  }
  function retryFailed() {
    for (const [key, entry] of known) if (entry.status === 'failed') known.delete(key);
  }
  function clear() { generation++; known.clear(); queue.length = 0; request = null; }
  return {get, remember, retryFailed, clear, activate(next) { clear(); request = next; }};
}
