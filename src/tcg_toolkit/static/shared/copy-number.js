// Card numbers everywhere copy themselves on click, with a short "Copied" bubble.
import {t} from '../i18n.js';

// The Clipboard API needs a secure context; plain-HTTP deployments fall back to execCommand.
export async function copyText(text, {nav = globalThis.navigator, doc = globalThis.document, secure = globalThis.isSecureContext} = {}) {
  if (secure && nav?.clipboard?.writeText) return nav.clipboard.writeText(text);
  const area = doc.createElement('textarea');
  area.value = text; area.setAttribute('readonly', '');
  area.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
  doc.body.append(area); area.select();
  try {
    if (!doc.execCommand('copy')) throw new Error('copy_failed');
  } finally { area.remove(); }
}

const timers = new WeakMap();
function label(button, number) {
  button.setAttribute('aria-label', t('copy_number', {number}));
  button.title = t('copy_number', {number});
}
// Works for buttons made here and for the static preview-dialog button.
export function bindCopy(button, getNumber, copy = copyText) {
  button.addEventListener('click', async event => {
    // Never also trigger a surrounding card or row action.
    event.stopPropagation();
    const number = getNumber();
    if (!number) return;
    let key = 'number_copied';
    try { await copy(number); } catch { key = 'number_copy_failed'; }
    button.dataset.copied = t(key);
    button.classList.toggle('copy-failed', key !== 'number_copied');
    const status = document.getElementById('copy-status');
    if (status) status.textContent = t(key) + ' ' + number;
    clearTimeout(timers.get(button));
    timers.set(button, setTimeout(() => { delete button.dataset.copied; button.classList.remove('copy-failed'); }, 1400));
  });
}
export function numberButton(number, className = '') {
  const button = document.createElement('button');
  button.type = 'button'; button.className = `mono copy-number ${className}`.trim();
  button.textContent = number;
  label(button, number);
  bindCopy(button, () => number);
  return button;
}
export function relabel(button, number) { label(button, number); }
