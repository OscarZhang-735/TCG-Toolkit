// YuYu-Tei cart state for search results: per-product quantities, +1 / −1 actions.
// The shop is the source of truth; every change is reported back by the server.
import {createMessages, messageTier} from './notices.js';
const GAME_IDS = {ws: 7, chaos: 8, pm: 11, lycee: 34, sev: 47, wsr: 56};
const GAME_PATHS = {ws: 'ws', chaos: 'chaos', pm: 'pm', lycee: 'lo', sev: 'sev', wsr: 'wsr'};

// Prefer the hidden cart_* fields; older snapshots fall back to the product URL,
// whose /card/<ver>/<cid> path is the same identity the cart page links to.
export function cartTarget(item) {
  if (item.cart) return item.cart;
  try {
    const url = new URL(item.product_url);
    const match = url.hostname === 'yuyu-tei.jp' && url.pathname.match(/^\/sell\/([A-Za-z0-9_-]+)\/card\/([A-Za-z0-9_.-]+)\/([0-9]+)\/?$/);
    if (match && match[1] === GAME_PATHS[item.game]) return {gid: GAME_IDS[item.game], ver: match[2], cid: match[3], kizu: 0};
  } catch { /* not a product URL */ }
  return null;
}
export const productKey = target => JSON.stringify([target.gid, target.ver, target.cid, target.kizu]);

export function cartErrorKey(error) {
  if (error?.message === 'yuyutei_credentials_missing') return 'cart_need_credentials';
  const detail = error?.detail, type = typeof detail === 'string' ? detail : detail?.type;
  if (detail?.outcome === 'partial') return 'cart_partial';
  return ({cart_limit_reached: 'cart_limit_reached', yuyutei_credentials_rejected: 'cart_credentials_rejected', yuyutei_cart_rejected: 'cart_rejected',
    yuyutei_quantity_mismatch: 'cart_unverified', yuyutei_timeout: 'cart_unverified',
    yuyutei_cart_item_not_found: 'cart_out_of_date', yuyutei_cart_item_ambiguous: 'cart_ambiguous',
    invalid_yuyutei_request: 'cart_invalid_credentials',
    cart_operation_pending: 'cart_unverified'})[type]
    ?? (detail?.outcome === 'unknown' ? 'cart_unverified' : 'cart_failed');
}

// randomUUID needs a secure context; plain-HTTP deployments still have getRandomValues.
const randomKey = () => globalThis.crypto.randomUUID?.()
  ?? [...globalThis.crypto.getRandomValues(new Uint8Array(16))].map(b => b.toString(16).padStart(2, '0')).join('');

// Limits of what one request can do (see yuyutei_cart.py): an add sends 1–99 cards,
// lowering re-adds the target, so a lowered row ends at 0–98.
export const MAX_ADD = 99, MAX_LOWERED = 98;
// What the user typed in a quantity box: whole numbers only; IME full-width digits count.
export function parseQuantity(text) {
  const ascii = String(text ?? '').trim().replace(/[０-９]/g, d => String.fromCharCode(d.charCodeAt(0) - 0xFEE0));
  return /^[0-9]{1,9}$/.test(ascii) ? Number(ascii) : null;
}
// Allowed range for a product (search) or one cart row (type given). `stock` caps the product total.
export function quantityRange(rows, {type = null, stock = null} = {}) {
  const row = type ? rows.find(item => item.type === type) : rows.find(item => item.type === 'normal') ?? rows[0];
  const total = rows.reduce((sum, item) => sum + item.quantity, 0);
  const current = type ? (row?.quantity ?? 0) : total;
  // Only this row is lowered; other rows of the product (pre-orders) stay as they are.
  const others = type ? 0 : total - (row?.quantity ?? 0);
  const lowerable = Boolean(row) && row.removable !== false;
  const min = lowerable ? others : current;
  let max = current + MAX_ADD;
  if (stock != null) max = Math.max(current, Math.min(max, stock));
  return {row, current, others, min, max, stockLimited: stock != null && max === Math.max(current, stock)};
}
// Turns a desired number into what is actually sent, and the note to show when it was adjusted.
export function planQuantity(range, desired) {
  const {current, others, min, max, stockLimited} = range;
  if (desired > max) return {value: max, note: stockLimited ? {key: 'cart_qty_max_stock', params: {max}} : {key: 'cart_qty_max', params: {max, step: MAX_ADD}}};
  if (desired < min) return {value: min, note: {key: min === current ? 'cart_qty_cannot_lower' : 'cart_qty_min', params: {min}}};
  // A row above 99 cannot be lowered to anything over 98 in one step.
  if (desired < current && desired - others > MAX_LOWERED) return {value: current, note: {key: 'cart_qty_lower_limit', params: {max: others + MAX_LOWERED}}};
  return {value: desired, note: null};
}

export function createCart(client, onView = () => {}, newKey = randomKey) {
  // Views redraw on every change; a failing view must never leave state (e.g. busy) half-updated.
  const onChange = () => { try { onView(); } catch (error) { console.error(error); } };
  // generation: signed-in user; reads: latest cart query (older replies are dropped).
  let generation = 0, reads = 0;
  const messages = createMessages(onChange);
  // notice: an account-level problem (credentials) shown once for the page, not per card.
  const state = {configured: false, loading: false, error: '', rows: [], totalQuantity: null,
    subtotal: null, complete: true, busy: new Set(), messages: messages.map, notice: ''};
  function report(key, message) {
    if (messageTier(message) === 'account') state.notice = message;
    else messages.set(key, message);
  }
  function apply(cart) {
    state.rows = cart.items; state.totalQuantity = cart.total_quantity;
    state.subtotal = cart.subtotal; state.complete = cart.complete;
  }
  function configured() {
    try { return client.hasCredentials(); } catch { return false; }
  }
  function clear({syncCredentials = false} = {}) {
    generation++;
    messages.clear();
    Object.assign(state, {configured: false, loading: false, error: '', rows: [], totalQuantity: null,
      subtotal: null, complete: true, busy: new Set(), notice: ''});
    if (syncCredentials) state.configured = configured();
    onChange();
  }
  async function refresh() {
    const current = generation, read = ++reads;
    state.configured = configured(); state.error = '';
    if (!state.configured) { state.rows = []; state.totalQuantity = state.subtotal = null; onChange(); return; }
    state.loading = true; onChange();
    try {
      const cart = await client.query();
      if (current === generation && read === reads) apply(cart);
    } catch (error) {
      if (current === generation && read === reads && error.name !== 'AbortError') state.error = cartErrorKey(error);
    }
    if (current === generation && read === reads) { state.loading = false; onChange(); }
  }
  const rowsFor = target => state.rows.filter(row => productKey(row) === productKey(target));
  const quantity = target => rowsFor(target).reduce((sum, row) => sum + row.quantity, 0);
  async function operate(target, action) {
    const key = productKey(target), current = generation;
    if (state.busy.has(key)) return;
    state.busy.add(key); messages.remove(key); onChange();
    let resync = false;
    try {
      // An action returns true when its local result is incomplete and needs a re-read.
      resync = Boolean(await action());
      // This reply is newer than any query still in flight.
      reads++; state.loading = false; state.error = ''; state.notice = '';
    } catch (error) {
      if (current !== generation || error.name === 'AbortError') return;
      report(key, cartErrorKey(error));
      // The outcome may differ from what we believe: read the real cart again.
      resync = error.status !== 422 && error.message !== 'yuyutei_credentials_missing';
    } finally {
      if (current === generation) { state.busy.delete(key); onChange(); }
    }
    if (resync && current === generation) await refresh();
  }
  // `item` is a search result (or cart row) describing a product the cart may not list yet.
  async function addCount(target, item, count) {
    const beforeRows = rowsFor(target).map(row => ({...row}));
    let result;
    try { result = await client.add({...target, counter: count}, newKey()); }
    catch (error) {
      const d = error.detail;
      const limited = d?.type === 'yuyutei_quantity_mismatch' && d.requested_quantity === count
        && Number.isInteger(d.before_quantity) && Number.isInteger(d.after_quantity)
        && d.after_quantity >= d.before_quantity && d.after_quantity - d.before_quantity < count;
      if (!limited) throw error;
      // A capped add may have partly succeeded. Restore the previous amount,
      // rather than displaying a rollback while leaving extra cards in the shop.
      if (d.after_quantity > d.before_quantity) {
        const snapshot = await client.query();
        const rows = snapshot.items.filter(row => productKey(row) === productKey(target));
        const delta = d.after_quantity - d.before_quantity;
        const changed = rows.filter(row => row.quantity !== (beforeRows.find(old => old.type === row.type)?.quantity ?? 0));
        const oldTotal = beforeRows.reduce((sum, row) => sum + row.quantity, 0);
        if (oldTotal !== d.before_quantity || rows.reduce((sum, row) => sum + row.quantity, 0) !== d.after_quantity
            || changed.length !== 1 || changed[0].quantity - (beforeRows.find(row => row.type === changed[0].type)?.quantity ?? 0) !== delta
            || changed[0].quantity - delta > MAX_LOWERED) throw error;
        const restored = await client.setQuantity({...target, type: changed[0].type}, changed[0].quantity - delta);
        apply(restored.cart);
      }
      error.detail = {type: 'cart_limit_reached'};
      throw error;
    }
    // Reflect the verified count locally instead of re-reading the whole cart.
    const rows = rowsFor(target), normal = rows.find(row => row.type === 'normal');
    // Only reserved rows: which row grew is unknown, so read the cart again.
    if (rows.length && !normal) return true;
    const own = result.after_quantity - rows.filter(row => row !== normal).reduce((sum, row) => sum + row.quantity, 0);
    if (normal) normal.quantity = own;
    else state.rows.push({...target, type: 'normal', quantity: own, unit_price: item.price ?? item.unit_price ?? 0,
      name: item.name ?? '', game: GAME_PATHS[item.game] ?? item.game ?? '', product_url: item.product_url ?? '',
      image_url: item.image_url ?? null, card_number: item.card_number ?? null, rarity: item.rarity ?? null, removable: true});
    if (state.complete) {
      state.totalQuantity = state.rows.reduce((sum, row) => sum + row.quantity, 0);
      state.subtotal = state.rows.reduce((sum, row) => sum + row.quantity * row.unit_price, 0);
    }
  }
  function increase(target, item = {}) {
    return operate(target, () => addCount(target, item, 1));
  }
  const range = (target, options) => quantityRange(rowsFor(target), options);
  // Typed quantity: clamp to what is allowed, then add the difference or lower the row.
  async function setTo(target, desired, {type = null, stock = null, item = {}} = {}) {
    const key = productKey(target), limits = range(target, {type, stock});
    const {value, note} = planQuantity(limits, desired);
    if (value !== limits.current) {
      await operate(target, () => value > limits.current
        ? addCount(target, item, value - limits.current)
        : client.setQuantity({...target, type: limits.row.type}, value - limits.others).then(result => { apply(result.cart); }));
    }
    // The adjustment note outlives the operation's own message reset; errors take precedence.
    if (note && !messages.has(key)) { messages.set(key, note); onChange(); }
    return value;
  }
  // Without a type, the normal row goes first; reserved rows are lowered once it is gone.
  function decrease(target, type = null) {
    const rows = rowsFor(target);
    const row = type ? rows.find(item => item.type === type) : rows.find(item => item.type === 'normal') ?? rows[0];
    if (!row) return Promise.resolve();
    return operate(target, async () => {
      const result = await client.setQuantity({...target, type: row.type}, row.quantity - 1);
      apply(result.cart);
    });
  }
  function remove(target, type) {
    return operate(target, async () => {
      const result = await client.remove({...target, type});
      apply(result.cart);
    });
  }
  return {state, refresh, clear, quantity, dismissNotice() { state.notice = ''; onChange(); }, increase, decrease, remove, setTo, range,
    isBusy: target => state.busy.has(productKey(target)),
    // When this card's bubble started; undefined once the bubble is over.
    bubbleStarted: target => messages.started(productKey(target)),
    message: target => messages.get(productKey(target))};
}
