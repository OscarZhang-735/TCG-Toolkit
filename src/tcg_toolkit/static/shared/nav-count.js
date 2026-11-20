// Sidebar count bubbles (favorites, card library, cart): hidden at 0, capped at 9999+.
import {getLanguage} from '../i18n.js';

export function navCountText(count, language = getLanguage()) {
  return count > 9999 ? '9999+' : count.toLocaleString(language);
}
export function setNavCount(element, count) {
  const shown = Number.isFinite(count) && count > 0;
  element.hidden = !shown;
  element.textContent = shown ? navCountText(count) : '';
  // The exact figure stays available when the bubble is capped.
  element.title = shown && count > 9999 ? count.toLocaleString(getLanguage()) : '';
}
