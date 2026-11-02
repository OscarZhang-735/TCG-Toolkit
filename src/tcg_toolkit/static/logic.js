export function selectItems(items, filters) {
  const text = filters.text.trim().toLocaleLowerCase();
  const result = items.filter(item => filters.games.includes(item.game)
    && (!filters.stock || item.stock_status === filters.stock)
    && (!filters.rarity || (filters.rarity === '__unknown__' ? !item.rarity : item.rarity === filters.rarity))
    && (!filters.subscription || filters.isSubscribed(item) === (filters.subscription === 'subscribed'))
    && (filters.min === null || item.price >= filters.min)
    && (filters.max === null || item.price <= filters.max)
    && (!text || `${item.name} ${item.card_number}`.toLocaleLowerCase().includes(text)));
  if (filters.sort === 'original') return result;
  const [key, direction] = filters.sort.split(':');
  const collator = new Intl.Collator('en', {numeric: true, sensitivity: 'base'});
  return result.sort((a, b) => {
    if (a[key] === null) return b[key] === null ? 0 : 1;
    if (b[key] === null) return -1;
    const difference = typeof a[key] === 'number' ? a[key] - b[key] : collator.compare(a[key], b[key]);
    return direction === 'desc' ? -difference : difference;
  });
}

export async function loadResults({games, keyword, signal, fetchPage, onPage, onError, wait}) {
  const queue = games.map(game => ({game, page: 1}));
  let failed = false;
  while (queue.length && !signal.aborted) {
    const {game, page} = queue.shift();
    try {
      const result = await fetchPage(game, keyword, page, signal);
      if (signal.aborted) break;
      onPage(result);
      if (result.next_page !== null) queue.push({game, page: result.next_page});
    } catch (error) {
      if (signal.aborted) break;
      failed = true;
      onError(game, error);
    }
    if (queue.length && !signal.aborted) await wait(signal);
  }
  return failed;
}

// Price slider positions are indexes into the distinct loaded prices, so skewed
// price lists stay usable; a thumb at either end means "no limit".
export function priceSteps(items) {
  return [...new Set(items.map(item => item.price))].sort((a, b) => a - b);
}
export function sliderIndex(steps, value, edge) {
  const last = Math.max(steps.length - 1, 0);
  if (value === null) return edge === 'min' ? 0 : last;
  if (edge === 'min') { const index = steps.findIndex(price => price >= value); return index === -1 ? last : index; }
  const index = steps.findLastIndex(price => price <= value);
  return index === -1 ? 0 : index;
}
export function sliderBound(steps, index, edge) {
  if (steps.length < 2 || index <= 0 && edge === 'min' || index >= steps.length - 1 && edge === 'max') return null;
  return steps[index];
}

// YuYu-Tei serves 100x140 thumbnails; the same path under /front/ is the 500x700 image.
// The noimage placeholder has no larger version, so it is not previewable.
export function largeImageUrl(url) {
  if (!url || /\/noimage[^/]*$/.test(url)) return null;
  return url.replace('/100_140/', '/front/');
}
