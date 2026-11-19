// Square avatar crop geometry. A crop is {zoom, cx, cy}: the centre of the selection in
// image pixels and a zoom factor where 1 means the square just fits the image's short side.
export const AVATAR_SIZE = 256;
export const MAX_ZOOM = 4;

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const side = (image, zoom) => Math.min(image.width, image.height) / zoom;

export function initialCrop(image) {
  return {zoom: 1, cx: image.width / 2, cy: image.height / 2};
}

// Keep the zoom in range and the selection fully inside the image.
export function clampCrop(image, crop) {
  const zoom = clamp(Number.isFinite(crop.zoom) ? crop.zoom : 1, 1, MAX_ZOOM), half = side(image, zoom) / 2;
  return {zoom, cx: clamp(crop.cx, half, image.width - half), cy: clamp(crop.cy, half, image.height - half)};
}

// The source square to draw, in image pixels.
export function cropSource(image, crop) {
  const {zoom, cx, cy} = clampCrop(image, crop), size = side(image, zoom);
  return {x: cx - size / 2, y: cy - size / 2, size};
}

// Dragging the picture by (dx, dy) viewport pixels moves the selection the other way.
export function panCrop(image, crop, dx, dy, view) {
  const ratio = side(image, clampCrop(image, crop).zoom) / view;
  return clampCrop(image, {...crop, cx: crop.cx - dx * ratio, cy: crop.cy - dy * ratio});
}

// Zoom while keeping the image point under the anchor (fractions of the viewport, 0..1) fixed.
export function zoomCrop(image, crop, zoom, anchor = {x: .5, y: .5}) {
  const before = cropSource(image, crop), next = clamp(zoom, 1, MAX_ZOOM), size = side(image, next);
  const px = before.x + anchor.x * before.size, py = before.y + anchor.y * before.size;
  return clampCrop(image, {zoom: next, cx: px + (.5 - anchor.x) * size, cy: py + (.5 - anchor.y) * size});
}

// Draw the selection into a square canvas context of `size` pixels.
export function drawCrop(context, source, image, crop, size) {
  const {x, y, size: span} = cropSource(image, crop);
  context.clearRect(0, 0, size, size);
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = 'high';
  context.drawImage(source, x, y, span, span, 0, 0, size, size);
}
