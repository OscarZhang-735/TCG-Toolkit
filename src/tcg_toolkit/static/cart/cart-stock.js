import {productKey} from '../catalog/product-identity.js';

// Explicit fresh product-page queries, deduplicated by product and condition.
export function createCartStock(onChange = () => {}, concurrency = 3) {
  let request = null, generation = 0, active = 0;
  const entries = new Map(), queue = [];
  function get(target) { return entries.get(productKey(target)) ?? {status: 'idle'}; }
  async function load(target, current, send) {
    try {
      const params = new URLSearchParams({...target, stock: 'true'});
      const response = await send(`/api/yuyutei/product?${params}`, {cache: 'no-store'});
      if (!response.ok) throw new Error('stock_failed');
      const data = await response.json();
      if (current !== generation) return;
      const quantity = Number.isInteger(data.stock_quantity) && data.stock_quantity >= 0 ? data.stock_quantity : null;
      entries.set(productKey(target), {status: 'done', quantity});
    } catch {
      if (current !== generation) return;
      entries.set(productKey(target), {status: 'failed'});
    }
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
    const unique = new Map(targets.map(target => [productKey(target), target]));
    for (const [key, target] of unique) {
      if (get(target).status === 'loading') continue;
      // Keep the last answer so the display does not blank while refreshing.
      const last = get(target);
      entries.set(key, {status: 'loading', last: last.status === 'done' ? last : null}); queue.push(target);
    }
    onChange(); pump();
  }
  function clear() { generation++; request = null; entries.clear(); queue.length = 0; }
  return {get, fetch: target => fetchAll([target]), fetchAll, clear,
    activate(send) { clear(); request = send; }};
}
