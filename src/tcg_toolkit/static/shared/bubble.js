// Local, text-only feedback anchored to any container. One bubble per anchor.
const active = new WeakMap();
export function showBubble(anchor, text, {tone = 'warning', duration = 2000, fade = 300, started = Date.now()} = {}) {
  active.get(anchor)?.();
  const elapsed = Date.now() - started;
  if (elapsed >= duration + fade) return () => {};
  const node = anchor.ownerDocument.createElement('span');
  const variant = ['warning', 'success', 'info', 'error'].includes(tone) ? tone : 'info';
  node.className = `feedback-bubble feedback-bubble--${variant}`;
  node.setAttribute('role', ['warning', 'error'].includes(variant) ? 'alert' : 'status');
  node.textContent = text;
  node.style.animationDelay = `${duration - elapsed}ms`;
  node.style.setProperty('--bubble-fade', `${fade}ms`);
  const hadAnchorClass = anchor.classList.contains('bubble-anchor');
  anchor.classList.add('bubble-anchor');
  let timer;
  const dismiss = () => {
    clearTimeout(timer);
    node.remove();
    if (active.get(anchor) === dismiss) {
      active.delete(anchor);
      if (!hadAnchorClass) anchor.classList.remove('bubble-anchor');
    }
  };
  active.set(anchor, dismiss);
  anchor.append(node);
  timer = setTimeout(dismiss, duration + fade - elapsed);
  timer.unref?.();
  return dismiss;
}
