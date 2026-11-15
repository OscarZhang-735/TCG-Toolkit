// Currencies a unit price may be entered in (the same ones the sidebar can display).
export const CURRENCIES = ['JPY', 'CNY', 'CAD'];
// Yen value of a card's price, for comparing prices entered in different currencies.
// rates[code] is how much of that currency one yen buys; null when unpriced or the rate is unknown.
export function yenPrice(row, rates) {
  if (row.price == null) return null;
  const currency = row.currency ?? 'JPY';
  if (currency === 'JPY') return row.price;
  return rates[currency] ? row.price / rates[currency] : null;
}
