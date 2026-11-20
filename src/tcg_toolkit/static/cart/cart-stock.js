import {cartProductUrl, productKey} from '../catalog/product-identity.js';

const storefront = {hs: 'hobbystation', cm: 'cardmax'};

function stockKey(target) {
  return storefront[target.gid] ? target.product_url.replace(/\/$/, '') : productKey(target);
}

// Explicit fresh product-page queries, deduplicated by product and condition.
export function createCartStock(onChange = () => {}, concurrency = 3) {
  let request = null, generation = 0, active = 0;
  const entries = new Map(), queue = [], waiting = new Map();
  function get(target) { return entries.get(stockKey(target)) ?? {status: 'idle'}; }
  async function load(target, current, send) {
    const key = stockKey(target);
    try {
      const response = await send(cartProductUrl(target, true), {cache: 'no-store'});
      if (!response.ok) throw new Error('stock_failed');
      const data = await response.json();
      if (current !== generation) return;
      const quantity = Number.isInteger(data.stock_quantity) && data.stock_quantity >= 0 ? data.stock_quantity : null;
      const entry = {status: 'done', quantity, stockStatus: data.stock_status};
      if (storefront[target.gid]) {
        if (data.stock_text) entry.stockText = data.stock_text;
        for (const field of ['game', 'card_number', 'rarity']) {
          if (data[field]) entry[field] = data[field];
        }
      }
      entries.set(key, entry);
    } catch {
      if (current !== generation) return;
      entries.set(key, {status: 'failed'});
    }
    waiting.get(key)?.resolve(entries.get(key));
    waiting.delete(key);
    onChange();
  }
  function pump() {
    while (request && queue.length && active < concurrency) {
      const current = generation;
      active++;
      load(queue.shift(), current, request).finally(() => { active--; pump(); });
    }
  }
  function fetchAll(targets) {
    if (!request) return;
    const unique = new Map(targets.map(target => [stockKey(target), target]));
    for (const [key, target] of unique) {
      if (get(target).status === 'loading') continue;
      // Keep the last answer so the display does not blank while refreshing.
      const last = get(target);
      entries.set(key, {status: 'loading', last: last.status === 'done' ? last : null}); queue.push(target);
    }
    onChange(); pump();
  }
  function fetch(target) {
    if (!request) return Promise.resolve({status: 'idle'});
    const key = stockKey(target);
    if (waiting.has(key)) return waiting.get(key).promise;
    const read = {};
    read.promise = new Promise(resolve => { read.resolve = resolve; });
    waiting.set(key, read);
    fetchAll([target]);
    return read.promise;
  }
  function clear() {
    generation++; request = null; entries.clear(); queue.length = 0;
    for (const read of waiting.values()) read.resolve({status: 'idle'});
    waiting.clear();
  }
  return {get, fetch, fetchAll, clear,
    activate(send) { clear(); request = send; }};
}
