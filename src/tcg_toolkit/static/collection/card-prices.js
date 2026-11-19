import {CURRENCIES} from './card-money.js';

// Price lookup (/api/prices). Merchants name games by lowercase id; library games use display
// names (WS EN, ChaosTCG), so map one to the other. Only YuYu-Tei exists for now.
export const PRICE_MERCHANT = 'yuyutei';
export function priceGame(game) {
  const id = (game ?? '').trim().toLowerCase().replace(/\s+/g, '_');
  return id === 'chaostcg' ? 'chaos' : id;
}
// Offers with the draft's rarity first, otherwise in the merchant's order.
export function rankOffers(items, rarity) {
  const wanted = (rarity ?? '').trim().toLowerCase();
  const match = item => Boolean(wanted) && (item.rarity ?? '').trim().toLowerCase() === wanted;
  return [...items].sort((a, b) => match(b) - match(a));
}
// The offer a bulk lookup uses for a card: same rarity (case-insensitive) and a currency the
// library supports; among several, the lowest price, preferring offers in stock. null when none.
export function pickOffer(items, rarity) {
  const wanted = (rarity ?? '').trim().toLowerCase();
  if (!wanted) return null;
  const matches = items.filter(item => (item.rarity ?? '').trim().toLowerCase() === wanted &&
    CURRENCIES.includes(item.currency) && Number.isFinite(item.price) && item.price >= 0);
  const inStock = matches.filter(item => item.stock_status === 'in_stock');
  return (inStock.length ? inStock : matches).reduce((best, item) => best && best.price <= item.price ? best : item, null);
}
const priceErrors = {price_source_timeout: 'price_lookup_timeout', price_source_unavailable: 'price_lookup_unavailable',
  price_source_unreadable: 'price_lookup_unreadable', unsupported_merchant_game: 'price_lookup_unsupported',
  unsupported_merchant: 'price_lookup_unsupported'};
// Merchants that can price cards: [{id, name, games: Set(['ws', ...])}].
export async function fetchPriceMerchants(api, signal) {
  const response = await api('/api/prices/merchants', {cache: 'no-store', signal});
  if (!response.ok) throw new Error('price merchants failed');
  const data = await response.json();
  return data.items.map(item => ({id: item.id, name: item.name, games: new Set(item.games)}));
}
// Resolves to the offers; failures throw with a translation key in error.key.
export async function lookupPrices(api, {game, card_number}, signal, merchant = PRICE_MERCHANT) {
  const query = new URLSearchParams({merchant, game: priceGame(game), card_number: card_number.trim()});
  const response = await api(`/api/prices?${query}`, {cache: 'no-store', signal});
  const data = await response.json().catch(() => null);
  if (!response.ok) throw Object.assign(new Error('price lookup failed'), {key: priceErrors[data?.detail] ?? 'price_lookup_failed'});
  return data.items;
}
