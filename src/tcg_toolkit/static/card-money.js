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
// Converted amounts round like shop prices (whole yen); entered amounts keep their cents.
export function formatMoney(amount, currency, language, converted = false) {
  const whole = currency === 'JPY' && (converted || Number.isInteger(amount));
  return new Intl.NumberFormat(language, {style: 'currency', currency, currencyDisplay: 'code',
    minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: whole ? 0 : 2}).format(converted && whole ? Math.round(amount) : amount);
}
// The price in the display currency with the entered amount underneath (also when the currencies
// match). Unpriced rows show a placeholder in the display currency ("JPY -"); when the rate is
// unknown only the entered amount is shown. Shared by the card library and graded cards.
export function priceDisplay(row, {currency, rates, language}) {
  const make = (tag, text = '', className = '') => {
    const el = document.createElement(tag); el.textContent = text; if (className) el.className = className; return el;
  };
  const box = make('span', '', 'converted-price');
  if (row.price == null) { box.append(make('span', `${currency} -`, 'muted')); return box; }
  const value = yenPrice(row, rates), original = formatMoney(row.price, row.currency, language);
  if (row.currency === currency) box.append(make('span', original), make('small', original, 'original-price'));
  else if (value == null) box.append(make('span', original));
  else box.append(make('span', formatMoney(value * rates[currency], currency, language, true)), make('small', original, 'original-price'));
  return box;
}
