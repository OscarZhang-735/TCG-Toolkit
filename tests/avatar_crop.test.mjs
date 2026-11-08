import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../src/tcg_toolkit/static/avatar-crop.js', import.meta.url), 'utf8');
const {AVATAR_SIZE, MAX_ZOOM, initialCrop, clampCrop, cropSource, panCrop, zoomCrop, drawCrop} =
  await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const wide = {width: 800, height: 400}, tall = {width: 300, height: 900};
const close = (actual, expected) => {
  for (const key of Object.keys(expected)) assert.ok(Math.abs(actual[key] - expected[key]) < 1e-9, `${key}: ${actual[key]} != ${expected[key]}`);
};

test('the initial crop is the centred square of the short side', () => {
  assert.equal(AVATAR_SIZE, 256);
  assert.deepEqual(cropSource(wide, initialCrop(wide)), {x: 200, y: 0, size: 400});
  assert.deepEqual(cropSource(tall, initialCrop(tall)), {x: 0, y: 300, size: 300});
  assert.deepEqual(cropSource({width: 50, height: 50}, initialCrop({width: 50, height: 50})), {x: 0, y: 0, size: 50});
});

test('zoom and centre stay inside the image', () => {
  assert.deepEqual(clampCrop(wide, {zoom: 0.2, cx: -50, cy: 999}), {zoom: 1, cx: 200, cy: 200});
  assert.deepEqual(clampCrop(wide, {zoom: 99, cx: 9999, cy: -1}), {zoom: MAX_ZOOM, cx: 750, cy: 50});
  assert.equal(clampCrop(wide, {zoom: NaN, cx: 400, cy: 200}).zoom, 1);
  const source = cropSource(wide, {zoom: 2, cx: 790, cy: 10});
  assert.deepEqual(source, {x: 600, y: 0, size: 200});
});

test('dragging moves the selection opposite to the pointer in image pixels', () => {
  // 400 image pixels shown in a 200px viewport: one viewport pixel is two image pixels.
  close(panCrop(wide, initialCrop(wide), 50, 0, 200), {zoom: 1, cx: 300, cy: 200});
  close(panCrop(wide, initialCrop(wide), -500, 0, 200), {cx: 600});
  close(panCrop(wide, initialCrop(wide), 0, 30, 200), {cy: 200});  // no vertical room at zoom 1
  close(panCrop(wide, {zoom: 2, cx: 400, cy: 200}, 0, 40, 200), {cy: 160});
});

test('zooming keeps the anchor point fixed', () => {
  const start = initialCrop(wide);
  close(zoomCrop(wide, start, 2), {zoom: 2, cx: 400, cy: 200});
  // Anchor at the top-left corner of the viewport: image point (200, 0) stays at the corner.
  const corner = zoomCrop(wide, start, 2, {x: 0, y: 0});
  close(cropSource(wide, corner), {x: 200, y: 0, size: 200});
  const point = {x: .75, y: .25}, before = cropSource(wide, {zoom: 1.5, cx: 400, cy: 200});
  const after = cropSource(wide, zoomCrop(wide, {zoom: 1.5, cx: 400, cy: 200}, 3, point));
  close({x: after.x + point.x * after.size, y: after.y + point.y * after.size},
    {x: before.x + point.x * before.size, y: before.y + point.y * before.size});
  // Zooming back out is clamped so the square never leaves the image.
  const out = cropSource(wide, zoomCrop(wide, {zoom: 4, cx: 750, cy: 50}, 1, {x: 1, y: 0}));
  assert.deepEqual(out, {x: 400, y: 0, size: 400});
  assert.equal(zoomCrop(wide, start, 10).zoom, MAX_ZOOM);
});

test('drawing copies the selected square into the output canvas', () => {
  const calls = [], context = {clearRect: (...args) => calls.push(['clear', ...args]), drawImage: (...args) => calls.push(['draw', ...args])};
  drawCrop(context, 'bitmap', tall, {zoom: 2, cx: 150, cy: 0}, AVATAR_SIZE);
  assert.equal(context.imageSmoothingQuality, 'high');
  assert.deepEqual(calls, [['clear', 0, 0, 256, 256], ['draw', 'bitmap', 75, 0, 150, 150, 0, 0, 256, 256]]);
});
