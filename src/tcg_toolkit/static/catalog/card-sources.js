// Inventory lookup source policy; ChaosTCG uses marketplace snapshots.
export function lookupSource(game, word) {
  const code = game.trim().toLowerCase().replace('sev en', 'sev_en').replace('ws en', 'ws_en');
  if (code === 'chaostcg' || code === 'chaos') return {
    marketplace: true, game: 'ChaosTCG',
    url: '/api/search?game=' + 'chaos' + '&page=1&keyword=' + encodeURIComponent(word)
  };
  if (['lycee', 'ws', 'wsr', 'pm', 'sev', 'sev_en', 'ws_en'].includes(code)) return {
    marketplace: false, url: '/api/official/' + code + '/search?word=' + encodeURIComponent(word)
  };
  return null;
}
export function lookupItems(source, items) {
  return source.marketplace ? items.map(item => ({game: source.game, card_number: item.card_number,
    name: item.name, rarity: item.rarity || '', image_url: item.image_url,
    source: 'yuyutei', is_official: false})) : items;
}
