// YuYu-Tei's order mail lists the unit price before 数量 and the line total after 小計.
const productLine = /^(\S+)\s+(\S+)\s+(.+?)\s+([^\s円]+)\s*円\s*数量\s*[:：]\s*(\S+?)(?:\s*小計\s*[:：]?\s*([^\s円]+)\s*円)?$/u;
const orderHeading = /^■?\s*ご注文商品\s*[:：]?$/u;
const divider = /^[-─━=－ー]{3,}$/u;

function wholeNumber(text) {
  const value = text.normalize('NFKC');
  if (!/^(?:\d+|\d{1,3}(?:,\d{3})+)$/.test(value)) return NaN;
  return Number(value.replaceAll(',', ''));
}

function parseProduct(text, line) {
  const match = productLine.exec(text);
  if (!match) return null;
  const [, printedNumber, rarity, name, unitPrice, count, subtotal] = match;
  const number = printedNumber.normalize('NFKC'), price = wholeNumber(unitPrice), quantity = wholeNumber(count);
  if (!/[\p{L}\p{N}]/u.test(number) || !Number.isSafeInteger(quantity) || quantity < 1 ||
      !Number.isInteger(price) || price > 999_999_999_999 ||
      (subtotal !== undefined && !Number.isFinite(wholeNumber(subtotal)))) return null;
  return {line, card_number: number, rarity, name, quantity, price, currency: 'JPY'};
}

export function parseYuyuteiOrder(text) {
  const lines = text.split(/\r\n?|\n/).map(line => line.trim().replace(/\\+$/, '').trimEnd());
  const hasHeading = lines.some(line => orderHeading.test(line));
  let inProducts = !hasHeading, sawProduct = false;
  const items = [], errors = [];
  for (const [index, text] of lines.entries()) {
    if (orderHeading.test(text)) { inProducts = true; sawProduct = false; continue; }
    if (!inProducts || !text) continue;
    if (divider.test(text) || text.startsWith('■')) {
      if (hasHeading && (sawProduct || text.startsWith('■'))) inProducts = false;
      continue;
    }
    const firstWord = text.split(/\s+/, 1)[0].normalize('NFKC');
    if (!/\p{N}/u.test(firstWord) && !/数量\s*[:：]/u.test(text)) continue;
    sawProduct = true;
    const line = index + 1, item = parseProduct(text, line);
    if (item) items.push(item);
    else errors.push({line, text});
  }
  return {items, errors};
}
