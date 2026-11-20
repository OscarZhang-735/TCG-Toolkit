// Renders one card message as short-lived feedback (see notices.js for the tiers).
import {showBubble} from './bubble.js';
import {messageTier} from './notices.js';

// Tapped dots re-show their bubble on the surrounding card or controls: a grid card's
// star sits inside an image that clips anything overflowing it.
const bubbleHost = host => host.closest?.('.card, .favorite-controls, .cart-controls') ?? host;
// A small dot in the corner of `host`. The text stays reachable: as a tooltip,
// for screen readers, and as a bubble again when the dot is tapped.
export function markDot(host, text, {tone = 'error', anchor = null} = {}) {
  host.classList.add('has-dot');
  const dot = host.ownerDocument.createElement('span');
  dot.className = `notice-dot notice-dot--${tone}`; dot.title = text;
  dot.addEventListener('click', event => { event.stopPropagation(); showBubble(anchor ?? bubbleHost(host), text, {tone: tone === 'error' ? 'error' : 'warning'}); });
  const hidden = host.ownerDocument.createElement('span');
  hidden.className = 'sr-only'; hidden.textContent = text;
  host.append(dot, hidden);
}

// `anchor` holds the bubble; `control` gets the dot an error leaves behind.
// `started` is when the message arrived (undefined once its bubble is over).
export function showFeedback(message, text, {anchor, control = anchor, started}) {
  const error = messageTier(message) === 'error';
  if (started !== undefined) showBubble(anchor, text, {tone: error ? 'error' : 'warning', started});
  if (error && control) markDot(control, text, {anchor});
}
