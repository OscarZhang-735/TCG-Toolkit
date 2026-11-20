import {t, getLanguage} from '../i18n.js';

const icons = {in_stock: 'bi-check-circle-fill', out_of_stock: 'bi-x-circle', unknown: 'bi-question-circle'};

export function stockBadge({source, stock_status: status, stock_quantity: quantity = null, stock_text: text}, cartQuantity = null) {
  const badge = document.createElement('span'), icon = document.createElement('i');
  const hobby = source === 'hobbystation';
  const short = quantity > 0 && cartQuantity !== null && quantity < cartQuantity;
  const count = quantity?.toLocaleString(getLanguage());
  badge.className = `badge ${status}${short && !hobby ? ' stock-short' : ''}`;
  icon.className = `bi ${short && !hobby ? 'bi-exclamation-triangle-fill' : icons[status]}`;
  badge.append(icon, t(short && !hobby ? 'cart_stock_short' : status) + (count && !hobby ? ` (${count})` : ''));
  if (short) badge.title = t('cart_stock_short_hint', {stock: count, quantity: cartQuantity.toLocaleString(getLanguage())});
  else if (hobby && quantity !== null) badge.title = t('stock_count_exact', {count});
  else if (status === 'in_stock' && quantity === null) {
    badge.title = t('stock_count_unknown');
    if (hobby && /[○◯◎]/.test(text ?? '')) badge.title += t('stock_count_unknown_hobbystation');
  }
  if (hobby && text && quantity === null) badge.title = [badge.title, text].filter(Boolean).join(' ');
  if (badge.title) {
    badge.classList.add('has-hint');
    badge.setAttribute('aria-label', t(status) + ' · ' + badge.title);
  }
  return badge;
}
