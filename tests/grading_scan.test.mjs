import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const asModule = value => `data:text/javascript;base64,${Buffer.from(value).toString('base64')}`;
const source = (await readFile(new URL('../src/tcg_toolkit/static/grading-scan.js', import.meta.url), 'utf8'))
  .replace("import {loadImage} from './avatar-crop-ui.js';", 'const loadImage = async file => file;');
const {parseScan, cropBox, soften, frameFrom, createDecoder, decodeSource, decodePhoto, cameraError, cameraSupported,
  startCamera, PHOTO_ATTEMPTS, CAMERA_ATTEMPTS} = await import(asModule(source));

// The registry's own patterns, as /api/grading/providers sends them: this also proves the
// Python patterns compile and match in a JavaScript RegExp.
const registry = await readFile(new URL('../src/tcg_toolkit/grading.py', import.meta.url), 'utf8');
const PROVIDERS = [...registry.matchAll(/GradingProvider\('(\w+)', '(\w+)', \w+, (\d+), \(\s*r'([^']+)'/g)]
  .map(([, id, name, timeout, pattern]) => ({id, name, timeout_seconds: Number(timeout), scan_patterns: [pattern]}));
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };

test('label QR links recognise provider and number, keeping leading zeros', () => {
  assert.deepEqual(PROVIDERS.map(provider => provider.id), ['psa', 'cgc', 'ars']);
  // Decoded from real slab labels.
  assert.deepEqual(parseScan('https://www.psacard.com/cert/98428602/', PROVIDERS), {provider: 'psa', cert_number: '98428602'});
  assert.deepEqual(parseScan('https://www.cgccards.com/certlookup/6091052028/10_0/', PROVIDERS), {provider: 'cgc', cert_number: '6091052028'});
  assert.deepEqual(parseScan('https://ars-grading.com/grading/searchNumber?id=047088950114', PROVIDERS), {provider: 'ars', cert_number: '047088950114'});
  for (const [text, provider, number] of [
    ['http://psacard.com/cert/65821932/psa', 'psa', '65821932'],
    ['  HTTPS://WWW.PSACARD.COM/en-CA/cert/98428602/psa  ', 'psa', '98428602'],
    ['https://www.psacard.com/cert/98428602', 'psa', '98428602'],
    ['https://www.cgccomics.com/certlookup/6091052028/', 'cgc', '6091052028'],
    ['https://ars-grading.com/grading/searchNumber?lang=en&id=047088950114#top', 'ars', '047088950114'],
  ]) assert.deepEqual(parseScan(text, PROVIDERS), {provider, cert_number: number}, text);
});

test('bare digits keep the chosen provider; anything else is not a certificate', () => {
  assert.deepEqual(parseScan(' 6091052-028 ', PROVIDERS), {cert_number: '6091052028'});
  assert.deepEqual(parseScan('0012 3456', PROVIDERS), {cert_number: '00123456'});
  for (const text of ['', '   ', 'https://psacard.com.evil.test/cert/1/', 'https://evil.test/?u=https://www.psacard.com/cert/1/',
    'https://www.psacard.com/pop/1/', 'https://example.com/cert/123/', 'ABC123', '1'.repeat(33), 'https://ars-grading.com/grading/searchNumber?id=']) {
    assert.equal(parseScan(text, PROVIDERS), null, text);
  }
});

test('patterns a browser cannot compile are skipped instead of breaking recognition', () => {
  const providers = [{id: 'odd', scan_patterns: ['(?P<n>[0-9]+)']}, ...PROVIDERS];
  assert.deepEqual(parseScan('https://www.psacard.com/cert/1/', providers), {provider: 'psa', cert_number: '1'});
  // Without the provider list a link is not recognised at all.
  assert.equal(parseScan('https://www.psacard.com/cert/1/'), null);
});

test('attempts crop the centre and only ever scale down', () => {
  assert.deepEqual(cropBox(1600, 1200, 0.5, 480), {sx: 400, sy: 300, sw: 800, sh: 600, dw: 480, dh: 360});
  assert.deepEqual(cropBox(300, 200, 1, 640), {sx: 0, sy: 0, sw: 300, sh: 200, dw: 300, dh: 200});
  assert.deepEqual(cropBox(1080, 1920, 0.75, 640), {sx: 135, sy: 240, sw: 810, sh: 1440, dw: 360, dh: 640});
  for (const attempt of [...PHOTO_ATTEMPTS, ...CAMERA_ATTEMPTS]) {
    assert.ok(attempt.crop > 0 && attempt.crop <= 1 && attempt.size >= 200 && attempt.size <= 800, JSON.stringify(attempt));
  }
});

test('softening turns pixels grey and spreads isolated dots', () => {
  const width = 5, height = 5, rgba = new Uint8ClampedArray(width * height * 4).fill(255);
  const centre = (2 * width + 2) * 4;
  rgba[centre] = rgba[centre + 1] = rgba[centre + 2] = 0;  // one black dot on white
  const grey = soften(rgba, width, height, 0);
  assert.equal(grey[centre], 0); assert.equal(grey[0], 255);
  assert.ok([...grey].every((value, index) => index % 4 !== 3 || value === 255), 'opaque');
  const blurred = soften(rgba, width, height, 1);
  assert.ok(blurred[centre] > 0 && blurred[centre] < 255);
  assert.ok(blurred[((2 * width) + 3) * 4] < 255, 'neighbour darkened');
  for (let i = 0; i < blurred.length; i += 4) assert.ok(blurred[i] === blurred[i + 1] && blurred[i] === blurred[i + 2]);
});

// Canvas stand-in recording which part of the source each attempt drew.
function fakeCanvas() {
  const draws = [];
  const context = {
    drawImage: (...args) => draws.push(args.slice(1)),
    getImageData: (x, y, width, height) => ({data: new Uint8ClampedArray(width * height * 4).fill(200), width, height}),
  };
  return {draws, getContext: () => context};
}

test('frames are drawn from the centre crop and softened when the attempt asks for it', () => {
  const canvas = fakeCanvas(), source = {naturalWidth: 1000, naturalHeight: 800};
  const plain = frameFrom(source, {crop: 1, size: 500, blur: 0}, canvas);
  assert.deepEqual(canvas.draws[0], [0, 0, 1000, 800, 0, 0, 500, 400]);
  assert.equal(plain.width, 500); assert.equal(plain.height, 400);
  const soft = frameFrom({videoWidth: 1280, videoHeight: 720}, {crop: 0.5, size: 480, blur: 1}, canvas);
  assert.deepEqual(canvas.draws[1], [320, 180, 640, 360, 0, 0, 480, 270]);
  assert.equal(soft.data.length, 480 * 270 * 4);
});

test('native detection is used when it reads QR codes; otherwise jsQR is loaded', async () => {
  class Native {
    static async getSupportedFormats() { return ['ean_13', 'qr_code']; }
    constructor(options) { assert.deepEqual(options, {formats: ['qr_code']}); }
    async detect(sourceImage) { return sourceImage === 'slab' ? [{rawValue: ''}, {rawValue: 'https://www.psacard.com/cert/1/'}] : []; }
  }
  let loads = 0;
  const native = await createDecoder({Detector: Native, loadJsQR: async () => { loads++; }});
  assert.equal(native.native, true);
  assert.equal(await native.detect('slab'), 'https://www.psacard.com/cert/1/');
  assert.equal(await native.detect('blank'), null);
  assert.equal(loads, 0);

  class BarcodesOnly extends Native { static async getSupportedFormats() { return ['ean_13']; } }
  const calls = [];
  const jsQR = (data, width, height, options) => { calls.push([width, height, options.inversionAttempts]); return width === 2 ? {data: 'text'} : null; };
  for (const Detector of [BarcodesOnly, undefined]) {
    const decoder = await createDecoder({Detector, loadJsQR: async () => jsQR});
    assert.equal(decoder.native, false);
    assert.equal(await decoder.detect('slab'), null);
    assert.equal(await decoder.decode({data: new Uint8ClampedArray(16), width: 2, height: 2}), 'text');
    assert.equal(await decoder.decode({data: new Uint8ClampedArray(36), width: 3, height: 3}, {invert: true}), null);
  }
  assert.deepEqual(calls.slice(0, 2), [[2, 2, 'dontInvert'], [3, 3, 'attemptBoth']]);

  for (const loadJsQR of [async () => { throw new Error('offline'); }, async () => undefined]) {
    await assert.rejects(createDecoder({Detector: undefined, loadJsQR}), error => error.key === 'graded_scan_load_failed');
  }
});

test('decoding tries the source, then each attempt in order, stopping at the first text', async () => {
  const tried = [];
  const decoder = {native: true, detect: async () => null,
    decode: async (frame, attempt) => { tried.push(attempt.size); return attempt.size === 640 ? 'found' : null; }};
  const attempts = [{crop: 1, size: 480, blur: 0}, {crop: 1, size: 640, blur: 0}, {crop: 1, size: 800, blur: 0}];
  const text = await decodeSource({naturalWidth: 900, naturalHeight: 900}, {decoder, attempts, canvas: fakeCanvas(), pause: async () => {}});
  assert.equal(text, 'found');
  assert.deepEqual(tried, [480, 640]);
  const failing = {native: false, detect: async () => null, decode: async () => { throw new Error('decoder bug'); }};
  assert.equal(await decodeSource({naturalWidth: 9, naturalHeight: 9}, {decoder: failing, attempts, canvas: fakeCanvas(), pause: async () => {}}), null);
  const direct = {native: true, detect: async () => 'native', decode: async () => assert.fail('not needed')};
  assert.equal(await decodeSource({}, {decoder: direct, attempts, canvas: fakeCanvas()}), 'native');
});

test('photos that are not images are reported; readable ones are decoded', async () => {
  const decoder = {native: false, detect: async () => null, decode: async () => 'https://ars-grading.com/grading/searchNumber?id=1'};
  await assert.rejects(decodePhoto('file', {decoder, load: async () => { throw new Error('bad'); }, canvas: fakeCanvas()}),
    error => error.key === 'graded_photo_unreadable');
  assert.equal(await decodePhoto('file', {decoder, load: async () => ({naturalWidth: 10, naturalHeight: 10}), canvas: fakeCanvas(), pause: async () => {}}),
    'https://ars-grading.com/grading/searchNumber?id=1');
});

test('camera errors map to messages and live scanning needs a secure context', () => {
  assert.equal(cameraError({name: 'NotAllowedError'}), 'graded_scan_denied');
  assert.equal(cameraError({name: 'NotFoundError'}), 'graded_scan_no_camera');
  assert.equal(cameraError({name: 'NotReadableError'}), 'graded_scan_camera_busy');
  assert.equal(cameraError(new Error('other')), 'graded_scan_failed');
  const media = {getUserMedia: async () => ({})};
  assert.equal(cameraSupported({isSecureContext: true, navigator: {mediaDevices: media}}), true);
  assert.equal(cameraSupported({isSecureContext: false, navigator: {mediaDevices: media}}), false);
  assert.equal(cameraSupported({isSecureContext: true, navigator: {}}), false);
});

test('the camera streams frames to the decoder until stopped, then releases the tracks', async () => {
  await assert.rejects(startCamera({}, {decoder: {}, onText() {}, scope: {isSecureContext: false}, canvas: fakeCanvas()}),
    error => error.key === 'graded_scan_insecure');
  const denied = {isSecureContext: true, navigator: {mediaDevices: {getUserMedia: async () => { throw Object.assign(new Error(), {name: 'NotAllowedError'}); }}}};
  await assert.rejects(startCamera({}, {decoder: {}, onText() {}, scope: denied, canvas: fakeCanvas()}), error => error.key === 'graded_scan_denied');

  let stopped = 0, frames = 0, constraints;
  const stream = {getTracks: () => [{stop: () => stopped++}]};
  const scope = {isSecureContext: true, navigator: {mediaDevices: {getUserMedia: async value => { constraints = value; return stream; }}}};
  const video = {readyState: 4, videoWidth: 1280, videoHeight: 720, play: async () => {}};
  const texts = [];
  const decoder = {native: false, detect: async () => null, decode: async () => (++frames === 3 ? 'https://www.psacard.com/cert/7/' : null)};
  const camera = await startCamera(video, {decoder, onText: text => texts.push(text), scope, canvas: fakeCanvas(),
    wait: () => new Promise(resolve => setImmediate(resolve))});
  assert.equal(constraints.video.facingMode.ideal, 'environment');
  assert.equal(video.srcObject, stream);
  for (let i = 0; i < 20 && !texts.length; i++) await settle();
  assert.deepEqual(texts, ['https://www.psacard.com/cert/7/']);
  camera.stop(); camera.stop();
  const seen = frames;
  await settle();
  assert.equal(stopped, 1);
  assert.equal(video.srcObject, null);
  assert.ok(frames <= seen + 1, 'no decoding after stop');

  const broken = {readyState: 0, play: async () => { throw new Error('autoplay blocked'); }};
  stopped = 0;
  await assert.rejects(startCamera(broken, {decoder, onText() {}, scope, canvas: fakeCanvas()}), error => error.key === 'graded_scan_failed');
  assert.equal(stopped, 1, 'tracks are released when playback fails');
});
