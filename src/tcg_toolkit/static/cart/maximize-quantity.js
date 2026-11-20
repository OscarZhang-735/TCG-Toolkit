// A rejected target is an exclusive upper bound. Each successful probe stays in
// the cart, so subsequent probes only add the difference from the verified total.
export async function maximizeQuantity(stock, readQuantity, add, maxAdd) {
  if (stock === 0) return;
  const initial = await readQuantity();
  let current = initial, rejected = Infinity, generalRefusal = null;
  while (current < (stock ?? Infinity) && current + 1 < rejected) {
    const next = rejected < Infinity ? current + Math.floor((rejected - current) / 2)
      : stock ?? (current < 10 ? 10 : current * 2);
    const count = Math.min(next - current, maxAdd), target = current + count;
    try {
      const actual = await add(count);
      if (actual !== target) throw unverifiedQuantity();
      current = actual;
    } catch (error) {
      const detail = error.detail;
      const limited = ['hobbystation_stock_exceeded', 'cardmax_stock_exceeded'].includes(detail?.type);
      const refused = detail?.type === 'hobbystation_cart_rejected' ||
        (detail?.type === 'yuyutei_cart_rejected' && /^ERROR\d*$/.test(detail.code) && detail.before_quantity === current);
      const short = shortAddQuantity(detail, current, count);
      if (!limited && !refused && short == null) throw error;

      // Even a rejected write can leave a partial addition. Only continue from
      // a complete cart that agrees with the shop's response.
      const actual = await readQuantity();
      if (actual !== (short ?? current)) throw unverifiedQuantity();
      current = actual;
      rejected = target;
      generalRefusal = refused ? error : null;
    }
  }
  // A generic rejection at every quantity is a product failure.
  if (generalRefusal && current === initial) throw generalRefusal;
}

function shortAddQuantity(detail, before, count) {
  if (!['yuyutei_quantity_mismatch', 'hobbystation_quantity_mismatch', 'cardmax_quantity_mismatch'].includes(detail?.type))
    return null;
  const sameRequest = detail.type === 'yuyutei_quantity_mismatch'
    ? detail.before_quantity === before && detail.requested_quantity === count
    : detail.expected_quantity === before + count;
  const after = detail.after_quantity;
  return sameRequest && Number.isInteger(after) && after >= before && after < before + count ? after : null;
}

export function unverifiedQuantity() {
  return Object.assign(new Error('cart_quantity_unverified'), {detail: {outcome: 'unknown'}});
}
