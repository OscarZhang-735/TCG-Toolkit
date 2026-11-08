import {AVATAR_SIZE, initialCrop, panCrop, zoomCrop, drawCrop} from './avatar-crop.js';

// Decode a picked file; browsers apply EXIF orientation to <img> and to drawImage().
// load/error rather than decode(): Chromium can leave decode() pending in a page that is not painting.
export async function loadImage(file) {
  const url = URL.createObjectURL(file), image = new Image();
  try {
    await new Promise((resolve, reject) => {
      image.addEventListener('load', resolve, {once: true});
      image.addEventListener('error', reject, {once: true});
      image.src = url;
    });
    if (!image.naturalWidth || !image.naturalHeight) throw new Error('empty image');
    return image;
  } finally { URL.revokeObjectURL(url); }
}

// Drag / wheel / pinch / keyboard cropping on a square stage that previews into a canvas.
export function createAvatarCropper({stage, canvas, zoom}) {
  let image = null, size = null, crop = null, pinch = null;
  const pointers = new Map();
  const view = () => stage.clientWidth || AVATAR_SIZE;
  const anchorAt = (x, y) => {
    const box = stage.getBoundingClientRect();
    return {x: Math.min(Math.max((x - box.left) / box.width, 0), 1), y: Math.min(Math.max((y - box.top) / box.height, 0), 1)};
  };
  function render() {
    if (!image) return;
    const pixels = Math.max(1, Math.round(view() * (globalThis.devicePixelRatio || 1)));
    if (canvas.width !== pixels) canvas.width = canvas.height = pixels;
    drawCrop(canvas.getContext('2d'), image, size, crop, pixels);
    zoom.value = String(crop.zoom);
  }
  const update = next => { crop = next; render(); };
  const zoomTo = (value, anchor) => update(zoomCrop(size, crop, value, anchor));
  const pinchState = () => {
    const [a, b] = [...pointers.values()];
    return {distance: Math.hypot(a.x - b.x, a.y - b.y) || 1, zoom: crop.zoom, anchor: anchorAt((a.x + b.x) / 2, (a.y + b.y) / 2)};
  };

  stage.addEventListener('pointerdown', event => {
    if (!image || (event.pointerType === 'mouse' && event.button !== 0)) return;
    stage.setPointerCapture(event.pointerId);
    pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
    pinch = pointers.size === 2 ? pinchState() : null;
    stage.classList.add('dragging');
  });
  stage.addEventListener('pointermove', event => {
    const last = pointers.get(event.pointerId);
    if (!last) return;
    const point = {x: event.clientX, y: event.clientY};
    pointers.set(event.pointerId, point);
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      zoomTo(pinch.zoom * Math.hypot(a.x - b.x, a.y - b.y) / pinch.distance, pinch.anchor);
    } else if (pointers.size === 1) update(panCrop(size, crop, point.x - last.x, point.y - last.y, view()));
  });
  const release = event => {
    pointers.delete(event.pointerId);
    pinch = pointers.size === 2 ? pinchState() : null;
    if (!pointers.size) stage.classList.remove('dragging');
  };
  stage.addEventListener('pointerup', release);
  stage.addEventListener('pointercancel', release);
  stage.addEventListener('wheel', event => {
    if (!image) return;
    event.preventDefault();
    zoomTo(crop.zoom * Math.exp(-event.deltaY * .0015), anchorAt(event.clientX, event.clientY));
  }, {passive: false});
  stage.addEventListener('keydown', event => {
    if (!image || event.altKey || event.ctrlKey || event.metaKey) return;
    const step = event.shiftKey ? 40 : 10;
    // Arrow keys move the picture, like dragging it.
    const moves = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]};
    if (moves[event.key]) update(panCrop(size, crop, ...moves[event.key], view()));
    else if (event.key === '+' || event.key === '=') zoomTo(crop.zoom * 1.1);
    else if (event.key === '-' || event.key === '_') zoomTo(crop.zoom / 1.1);
    else return;
    event.preventDefault();
  });
  zoom.addEventListener('input', () => { if (image) zoomTo(Number(zoom.value)); });
  new ResizeObserver(render).observe(stage);

  return {
    open(next) {
      image = next; size = {width: next.naturalWidth, height: next.naturalHeight};
      crop = initialCrop(size); pointers.clear(); pinch = null; render();
    },
    render,
    clear() {
      image = null; size = null; crop = null; pointers.clear(); pinch = null;
      canvas.width = canvas.height = 0; stage.classList.remove('dragging');
    },
    // A fixed-size square export; browsers without WebP encoding fall back to PNG.
    async export() {
      const output = document.createElement('canvas');
      output.width = output.height = AVATAR_SIZE;
      drawCrop(output.getContext('2d'), image, size, crop, AVATAR_SIZE);
      return new Promise(resolve => output.toBlob(resolve, 'image/webp', .92));
    },
  };
}
