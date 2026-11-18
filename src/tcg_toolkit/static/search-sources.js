// Merchants the search page can query and the games each supports. Mirrors
// SEARCH_SOURCES in search.py; null means every game.
export const SEARCH_SOURCES = {
  yuyutei: {label: 'source_yuyutei', games: null},
  hobbystation: {label: 'source_hobbystation', games: ['lycee', 'ws', 'wsr']},
  cardmax: {label: 'source_cardmax', games: ['ws', 'sev']},
};
export const DEFAULT_SOURCE = 'yuyutei';

export const supports = (source, game) => {
  const games = SEARCH_SOURCES[source]?.games;
  return games !== undefined && (games === null || games.includes(game));
};

// Every supported (game, source) pair, game-major so one game's merchants are
// fetched side by side. Unsupported pairs are skipped silently.
export function searchTargets(games, sources) {
  return games.flatMap(game => sources.filter(source => supports(source, game)).map(source => ({game, source})));
}

// Query string for /api/search; stock detail lookups are only meaningful for Card Max.
export function searchParams({game, source}, keyword, page, {cardmaxStock = false} = {}) {
  const params = {source, game, keyword, page};
  if (source === 'cardmax' && cardmaxStock) params.stock = 'true';
  return new URLSearchParams(params);
}

// Only YuYu-Tei results carry product identities that favorites and the cart accept.
export const isYuyutei = item => (item.source ?? DEFAULT_SOURCE) === DEFAULT_SOURCE;
