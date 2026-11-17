export async function fetchRate(target, request = fetch, onFetched = () => {}) {
  if (target === 'JPY') return 1;
  const response = await request(`/api/currency/convert?${new URLSearchParams({to: target, amount: 1})}`);
  const data = await response.json();
  if (!response.ok || data.from !== 'JPY' || data.to !== target || data.amount !== 1
      || typeof data.result !== 'number' || !Number.isFinite(data.result) || data.result <= 0) {
    throw new Error('currency_unavailable');
  }
  onFetched(data.fetched_at);
  return data.result;
}

export function formatPrice(jpy, currency, rate, language) {
  return new Intl.NumberFormat(language, {
    style: 'currency', currency, currencyDisplay: 'code',
    minimumFractionDigits: currency === 'JPY' ? 0 : 2,
    maximumFractionDigits: currency === 'JPY' ? 0 : 2,
  }).format(jpy * rate);
}

export function createRateCache(onChange) {
  let rates, pending, failed;
  function reset() {
    rates = {JPY: 1}; pending = new Set(); failed = new Set();
  }
  reset();
  function set(currency, rate) {
    rates[currency] = rate;
    failed.delete(currency);
  }
  function load(rows, request) {
    const current = rates;
    for (const currency of new Set(rows.filter(row => row.price != null).map(row => row.currency))) {
      if (currency in rates || pending.has(currency) || failed.has(currency)) continue;
      pending.add(currency);
      fetchRate(currency, request).then(rate => { if (current === rates) set(currency, rate); },
        () => { if (current === rates && !(currency in rates)) failed.add(currency); })
        .finally(() => { if (current === rates) { pending.delete(currency); onChange(); } });
    }
  }
  return {get rates() { return rates; }, set, load, reset};
}
