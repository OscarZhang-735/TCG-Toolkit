// What the user typed in a quantity box: whole numbers only; IME full-width digits count.
export function parseQuantity(text) {
  const ascii = String(text ?? '').trim().replace(/[０-９]/g, d => String.fromCharCode(d.charCodeAt(0) - 0xFEE0));
  return /^[0-9]{1,9}$/.test(ascii) ? Number(ascii) : null;
}
