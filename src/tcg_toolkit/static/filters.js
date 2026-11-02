import {t} from './i18n.js';
import {selectItems, priceSteps, sliderIndex, sliderBound} from './logic.js';

// Filter/sort panel shared by search results and favorites. Markup comes from
// <template id="filter-panel">; each instance keeps its own state. Passing
// isSubscribed adds the subscription filter (favorites only for now).
export function createFilterPanel(placeholder, {onChange, formatPrice, isSubscribed = null}) {
  const root = document.getElementById('filter-panel').content.firstElementChild.cloneNode(true);
  placeholder.replaceWith(root);
  const part = role => root.querySelector(`[data-role="${role}"]`);
  const text = part('text'), stock = part('stock'), rarity = part('rarity'), sort = part('sort'), subscription = part('subscription');
  part('subscription-field').hidden = !isSubscribed;
  part('subscription-field').parentElement.classList.toggle('with-subscription', Boolean(isSubscribed));
  const lo = part('price-min'), hi = part('price-max'), fill = part('price-fill'), label = part('price-label'), range = part('price-range');
  const games = () => [...root.querySelectorAll('[data-role="game"]:checked')].map(input => input.value);
  let steps = [], priceMin = null, priceMax = null;

  function syncSlider(items) {
    steps = priceSteps(items);
    const last = Math.max(steps.length - 1, 0);
    for (const input of [lo, hi]) { input.max = last; input.disabled = steps.length < 2; }
    lo.value = sliderIndex(steps, priceMin, 'min');
    hi.value = sliderIndex(steps, priceMax, 'max');
    const a = Number(lo.value), b = Number(hi.value), percent = index => last ? index / last * 100 : 0;
    fill.style.left = `${percent(a)}%`;
    fill.style.right = `${100 - (last ? percent(b) : 100)}%`;
    // Keep the min thumb reachable when both thumbs meet at the right end.
    lo.classList.toggle('on-top', a > last / 2);
    label.textContent = priceMin === null && priceMax === null ? t('any_price') : `${formatPrice(steps[a])} – ${formatPrice(steps[b])}`;
    range.classList.toggle('active', priceMin !== null || priceMax !== null);
  }
  function syncRarity(items) {
    const selected = rarity.value;
    const values = [...new Set(items.map(item => item.rarity).filter(Boolean))].sort();
    const options = [new Option(t('all'), ''), ...values.map(value => new Option(value, value))];
    if (items.some(item => !item.rarity)) options.push(new Option(t('unknown'), '__unknown__'));
    rarity.replaceChildren(...options); rarity.value = selected;
  }
  // Refresh the panel for these items and return the filtered, sorted matches.
  function apply(items) {
    syncRarity(items); syncSlider(items);
    return selectItems(items, {games: games(), stock: stock.value, rarity: rarity.value,
      subscription: isSubscribed ? subscription.value : '', isSubscribed,
      min: priceMin, max: priceMax, text: text.value, sort: sort.value});
  }
  function reset() {
    text.value = ''; stock.value = ''; rarity.value = ''; subscription.value = ''; sort.value = 'original';
    priceMin = priceMax = null;
    root.querySelectorAll('[data-role="game"]').forEach(input => { input.checked = true; });
    onChange();
  }

  root.querySelectorAll('input:not([type=range]), select').forEach(input => input.addEventListener('input', onChange));
  for (const [input, edge] of [[lo, 'min'], [hi, 'max']]) {
    input.addEventListener('input', () => {
      if (Number(lo.value) > Number(hi.value)) { if (edge === 'min') lo.value = hi.value; else hi.value = lo.value; }
      priceMin = sliderBound(steps, Number(lo.value), 'min');
      priceMax = sliderBound(steps, Number(hi.value), 'max');
      onChange();
    });
  }
  part('reset').addEventListener('click', reset);
  return {element: root, apply, reset};
}
