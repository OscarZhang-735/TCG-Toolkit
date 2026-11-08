// How per-card feedback is shown, so it never sits on the page for long:
// - flash:   already handled for the user (value clamped, cart re-read, old data kept).
//            A bubble for FLASH_MS, then the message is gone.
// - error:   this card needs attention. A bubble for FLASH_MS, then a small dot on the
//            control (full text in its tooltip) until the next action on that card.
// - account: the same cause would hit every card (credentials, email). Never per card:
//            one dismissable page notice instead.
export const FLASH_MS = 2300; // bubble.js: 2000 ms visible + 300 ms fade
const FLASH = new Set(['cart_qty_max_stock', 'cart_qty_max', 'cart_qty_min', 'cart_qty_cannot_lower', 'cart_qty_lower_limit',
  'cart_limit_reached', 'cart_unverified', 'cart_out_of_date',
  'favorite_updated', 'favorite_timeout', 'favorite_missing', 'favorite_parse', 'favorite_group_missing']);
const ACCOUNT = new Set(['cart_need_credentials', 'cart_credentials_rejected', 'cart_invalid_credentials', 'subscription_email_required']);
export const messageKey = message => typeof message === 'string' ? message : message?.key;
export function messageTier(message) {
  const key = messageKey(message);
  return ACCOUNT.has(key) ? 'account' : FLASH.has(key) ? 'flash' : 'error';
}

// Per-card messages with bubble timing. The start time survives re-renders, so a
// redraw continues the bubble instead of restarting it.
export function createMessages(onChange) {
  const messages = new Map(), timers = new Map();
  function stop(key) {
    const entry = timers.get(key);
    if (entry) clearTimeout(entry.timer);
    timers.delete(key);
  }
  function set(key, message) {
    stop(key); messages.set(key, message);
    const entry = {started: Date.now()}, flash = messageTier(message) === 'flash';
    entry.timer = setTimeout(() => {
      if (timers.get(key) !== entry) return;
      timers.delete(key);
      if (flash && messages.get(key) === message) messages.delete(key);
      onChange();
    }, FLASH_MS);
    entry.timer.unref?.();
    timers.set(key, entry);
  }
  function remove(key) { stop(key); messages.delete(key); }
  function clear() { for (const key of [...timers.keys()]) stop(key); messages.clear(); }
  return {map: messages, set, remove, clear, get: key => messages.get(key), has: key => messages.has(key),
    // When the bubble for this message started; undefined once its time is over.
    started: key => timers.get(key)?.started};
}
