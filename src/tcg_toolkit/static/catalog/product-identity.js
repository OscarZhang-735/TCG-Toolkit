const GAME_IDS = {ws: 7, chaos: 8, pm: 11, lycee: 34, sev: 47, wsr: 56};
export const GAME_PATHS = {ws: 'ws', chaos: 'chaos', pm: 'pm', lycee: 'lo', sev: 'sev', wsr: 'wsr'};

// Search offers carry cart fields, while product snapshots identify the normal
// offer by URL. Both resolve to the same shop product identity.
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

