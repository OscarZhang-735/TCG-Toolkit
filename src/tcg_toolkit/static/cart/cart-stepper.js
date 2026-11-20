// Quantity controls shared by search results and each shop's cart page.
import {t, getLanguage} from '../i18n.js';
import {productKey} from '../catalog/product-identity.js';
import {parseQuantity} from '../shared/quantity.js';
import {showFeedback} from '../shared/feedback.js';

const el = (tag, className = '', text = '') => {const node = document.createElement(tag); node.className = className; node.textContent = text; return node;};
const icon = name => el('i', `bi ${name}`);
const number = value => value.toLocaleString(getLanguage());

// Unsent typing survives re-renders (any cart change redraws the list).
const drafts = new Map();
export function cartStepper(cart, target, {shop, quantity, onIncrease, onDecrease, onSet, draftKey = productKey(target),
  increaseKey = 'cart_increase', canIncrease = true, canDecrease = true, canEdit = true}) {
  const box = el('div', 'cart-controls'), busy = cart.isBusy(target);
  const pending = busy || (cart.state.loading && !cart.state.rows.length);
  const stepper = el('div', `cart-stepper${quantity ? ' active' : ''}`); stepper.setAttribute('role', 'group');
  stepper.setAttribute('aria-label', t('cart_quantity_label', {quantity}));
  const button = (iconName, key, disabled, action) => {
    const node = el('button', 'cart-step'); node.type = 'button'; node.append(icon(iconName));
    node.title = t(key, {shop}); node.setAttribute('aria-label', node.title); node.disabled = disabled;
    node.addEventListener('click', action); return node;
  };
  let count;
  if (busy) { count = el('span', 'cart-qty'); count.append(icon('bi-arrow-repeat spin')); }
  else {
    count = document.createElement('input');
    count.className = 'cart-qty'; count.inputMode = 'numeric'; count.autocomplete = 'off';
    count.setAttribute('aria-label', t('cart_quantity_input')); count.title = t('cart_quantity_input');
    count.value = String(quantity); count.disabled = pending || !onSet || !canEdit;
    const draft = drafts.get(draftKey);
    if (draft && !count.disabled) {
      count.value = draft.value;
      // Re-rendered while typing: put the caret back once the box is in the page.
      requestAnimationFrame(() => { if (count.isConnected) { count.focus(); count.setSelectionRange(draft.start, draft.end); } });
    }
    const remember = () => drafts.set(draftKey, {value: count.value, start: count.selectionStart, end: count.selectionEnd});
    const commit = () => {
      drafts.delete(draftKey);
      const desired = parseQuantity(count.value);
      // Not a whole number, or unchanged: put the real quantity back and send nothing.
      if (desired === null || desired === quantity) { count.value = String(quantity); return; }
      onSet(desired);
    };
    count.addEventListener('focus', () => { if (!drafts.has(draftKey)) count.select(); });
    count.addEventListener('input', remember);
    count.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); commit(); count.blur(); }
      else if (event.key === 'Escape') { drafts.delete(draftKey); count.value = String(quantity); count.blur(); }
      else queueMicrotask(remember);
    });
    // A blur caused by the list redrawing is not a commit; the new box takes over the draft.
    count.addEventListener('blur', () => { if (count.isConnected && drafts.has(draftKey)) commit(); });
  }
  stepper.append(button('bi-dash-lg', 'cart_decrease', pending || !quantity || !canDecrease, onDecrease), count,
    button('bi-plus-lg', increaseKey, pending || !canIncrease, onIncrease));
  box.append(stepper);
  // No line of text under the stepper: a bubble while fresh, then a dot if it was an error.
  const message = cart.message(target);
  if (message) {
    const {key, params} = typeof message === 'string' ? {key: message, params: {}} : message;
    const text = t(key, {shop, ...Object.fromEntries(Object.entries(params).map(([k, v]) => [k, number(v)]))});
    showFeedback(message, text, {anchor: box, control: stepper, started: cart.bubbleStarted(target)});
  }
  return box;
}
