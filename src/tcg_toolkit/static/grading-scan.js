// QR scanning for graded slabs: recognising scanned text, and decoding camera frames
// and photos with the native BarcodeDetector or, where it has no QR support, the
// vendored jsQR (loaded on first use).
import {loadImage} from './avatar-crop-ui.js';

// Each attempt crops the centre of the source (fraction of each side), scales it so the
// longer side is at most `size` pixels and, with `blur`, softens a grey copy. Measured on
// real labels: ARS etches its QR as separate dots on brushed metal and CGC prints on a
// dotted gold pattern; neither decodes at full size, but both do once a box blur merges
// the dots. Small sizes also keep jsQR fast enough for the main thread.
export const PHOTO_ATTEMPTS = [
  {crop: 0.5, size: 480, blur: 1},
  {crop: 0.75, size: 640, blur: 1},
  {crop: 1, size: 640, blur: 0},
  {crop: 1, size: 800, blur: 1},
  {crop: 0.5, size: 800, blur: 2},
  {crop: 1, size: 320, blur: 0},
  {crop: 0.75, size: 480, blur: 1, invert: true},
];
// Live frames rotate through these, one per tick; the viewfinder asks for a centred code.
export const CAMERA_ATTEMPTS = [
  {crop: 0.5, size: 480, blur: 1},
  {crop: 0.75, size: 640, blur: 1},
  {crop: 1, size: 480, blur: 0},
  {crop: 0.5, size: 400, blur: 2},
];
const CAMERA_PAUSE_MS = 120;

const patternCache = new WeakMap();
function matchers(providers) {
  let list = patternCache.get(providers);
  if (!list) {
    list = [];
    for (const provider of providers) {
      for (const source of provider.scan_patterns ?? []) {
        // The server promises syntax both engines share; skip anything this browser rejects.
        try { list.push({provider: provider.id, pattern: new RegExp(source, 'i')}); } catch { /* ignored */ }
      }
    }
    patternCache.set(providers, list);
  }
  return list;
}

// A provider's certificate URL gives {provider, cert_number}; bare digits (a barcode or a
// typed number, spaces and dashes allowed) give {cert_number}; anything else is null.
// The URL is only read, never requested: the server builds the official address itself.
export function parseScan(text, providers = []) {
  const value = String(text ?? '').trim();
  if (!value) return null;
  for (const {provider, pattern} of matchers(providers)) {
    const match = pattern.exec(value);
    if (match?.[1]) return {provider, cert_number: match[1]};
  }
  const digits = value.replace(/[\s-]+/g, '');
  return /^[0-9]{1,32}$/.test(digits) ? {cert_number: digits} : null;
}

// Centre crop of crop × each side, scaled down (never up) to fit `size`.
export function cropBox(width, height, crop, size) {
  const sw = Math.max(1, Math.round(width * crop)), sh = Math.max(1, Math.round(height * crop));
  const scale = Math.min(1, size / Math.max(sw, sh));
  return {sx: Math.round((width - sw) / 2), sy: Math.round((height - sh) / 2), sw, sh,
    dw: Math.max(1, Math.round(sw * scale)), dh: Math.max(1, Math.round(sh * scale))};
}

function boxBlur(gray, width, height, radius) {
  const line = new Float32Array(gray.length), out = new Uint8ClampedArray(gray.length), span = radius * 2 + 1;
  const at = (value, limit) => Math.min(limit - 1, Math.max(0, value));
  for (let y = 0; y < height; y++) {
    const row = y * width;
    let sum = 0;
    for (let x = -radius; x <= radius; x++) sum += gray[row + at(x, width)];
    for (let x = 0; x < width; x++) {
      line[row + x] = sum / span;
      sum += gray[row + at(x + radius + 1, width)] - gray[row + at(x - radius, width)];
    }
  }
  for (let x = 0; x < width; x++) {
    let sum = 0;
    for (let y = -radius; y <= radius; y++) sum += line[at(y, height) * width + x];
    for (let y = 0; y < height; y++) {
      out[y * width + x] = sum / span;
      sum += line[at(y + radius + 1, height) * width + x] - line[at(y - radius, height) * width + x];
    }
  }
  return out;
}

// Grey RGBA copy of the pixels, blurred by two box passes of `radius` (about a Gaussian).
export function soften(rgba, width, height, radius) {
  let gray = new Uint8ClampedArray(width * height);
  for (let i = 0, j = 0; j < gray.length; i += 4, j++) gray[j] = (rgba[i] * 77 + rgba[i + 1] * 150 + rgba[i + 2] * 29) >> 8;
  if (radius > 0) gray = boxBlur(boxBlur(gray, width, height, radius), width, height, radius);
  const out = new Uint8ClampedArray(width * height * 4);
  for (let i = 0, j = 0; j < gray.length; i += 4, j++) { out[i] = out[i + 1] = out[i + 2] = gray[j]; out[i + 3] = 255; }
  return out;
}

const sourceSize = source => ({width: source.videoWidth || source.naturalWidth || source.width,
  height: source.videoHeight || source.naturalHeight || source.height});

// Draws one attempt's crop into the canvas and returns its pixels.
export function frameFrom(source, attempt, canvas) {
  const {width, height} = sourceSize(source);
  const box = cropBox(width, height, attempt.crop, attempt.size);
  canvas.width = box.dw; canvas.height = box.dh;
  const context = canvas.getContext('2d', {willReadFrequently: true});
  context.imageSmoothingEnabled = true; context.imageSmoothingQuality = 'high';
  context.drawImage(source, box.sx, box.sy, box.sw, box.sh, 0, 0, box.dw, box.dh);
  const image = context.getImageData(0, 0, box.dw, box.dh);
  if (!attempt.blur) return image;
  const data = soften(image.data, box.dw, box.dh, attempt.blur);
  return typeof ImageData === 'function' ? new ImageData(data, box.dw, box.dh) : {data, width: box.dw, height: box.dh};
}

const failure = (key, cause) => Object.assign(new Error(key), {key, cause});
const loadBundledJsQR = async () => { await import('./jsqr.js'); return globalThis.jsQR; };

// {native, detect(source), decode(frame, attempt)}: both resolve to the text or null.
export async function createDecoder({Detector = globalThis.BarcodeDetector, loadJsQR = loadBundledJsQR} = {}) {
  if (typeof Detector === 'function') {
    try {
      const formats = await Detector.getSupportedFormats?.();
      if (!formats || formats.includes('qr_code')) {
        const detector = new Detector({formats: ['qr_code']});
        const detect = async source => (await detector.detect(source)).find(code => code.rawValue)?.rawValue ?? null;
        return {native: true, detect, decode: frame => detect(frame)};
      }
    } catch { /* fall back to jsQR */ }
  }
  let jsQR;
  try { jsQR = await loadJsQR(); } catch (error) { throw failure('graded_scan_load_failed', error); }
  if (typeof jsQR !== 'function') throw failure('graded_scan_load_failed');
  return {native: false, detect: async () => null,
    decode: async (frame, attempt = {}) => jsQR(frame.data, frame.width, frame.height,
      {inversionAttempts: attempt.invert ? 'attemptBoth' : 'dontInvert'})?.data || null};
}

// Shared by every scanner on the page; a failed load is forgotten so it can be retried.
let sharedDecoder = null;
export function getDecoder(options) {
  sharedDecoder ??= createDecoder(options).catch(error => { sharedDecoder = null; throw error; });
  return sharedDecoder;
}

const yieldToPage = () => new Promise(resolve => setTimeout(resolve));

// Tries the source as is (native only), then each attempt in order; the first text wins.
export async function decodeSource(source, {decoder, attempts, canvas, pause = yieldToPage, cancelled = () => false}) {
  if (decoder.native) {
    const text = await decoder.detect(source).catch(() => null);
    if (text) return text;
  }
  for (const attempt of attempts) {
    if (cancelled()) return null;
    const text = await decoder.decode(frameFrom(source, attempt, canvas), attempt).catch(() => null);
    if (text) return text;
    await pause();  // keeps the page responsive between attempts
  }
  return null;
}

// Decodes a picked or captured photo; resolves to the text or null, rejects if unreadable.
export async function decodePhoto(file, {decoder, load = loadImage, canvas = document.createElement('canvas'),
  attempts = PHOTO_ATTEMPTS, pause} = {}) {
  let image;
  try { image = await load(file); } catch (error) { throw failure('graded_photo_unreadable', error); }
  return decodeSource(image, {decoder, attempts, canvas, pause});
}

export function cameraError(error) {
  return ({NotAllowedError: 'graded_scan_denied', SecurityError: 'graded_scan_denied',
    NotFoundError: 'graded_scan_no_camera', OverconstrainedError: 'graded_scan_no_camera',
    NotReadableError: 'graded_scan_camera_busy', AbortError: 'graded_scan_camera_busy'})[error?.name] ?? 'graded_scan_failed';
}

// Live camera scanning needs a secure context (HTTPS or localhost).
export const cameraSupported = (scope = globalThis) =>
  Boolean(scope.isSecureContext && scope.navigator?.mediaDevices?.getUserMedia);

// Streams the rear camera into `video` and reports every decoded text through onText until
// stop(). One attempt per tick; decoding never overlaps.
export async function startCamera(video, {decoder, onText, attempts = CAMERA_ATTEMPTS, scope = globalThis,
  canvas = document.createElement('canvas'), wait = ms => new Promise(resolve => setTimeout(resolve, ms))}) {
  if (!cameraSupported(scope)) throw failure('graded_scan_insecure');
  let stream;
  try {
    stream = await scope.navigator.mediaDevices.getUserMedia({audio: false,
      video: {facingMode: {ideal: 'environment'}, width: {ideal: 1280}, height: {ideal: 720}}});
  } catch (error) { throw failure(cameraError(error), error); }
  let stopped = false, tick = 0;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    for (const track of stream.getTracks()) track.stop();
    video.srcObject = null;
  };
  video.muted = true; video.playsInline = true; video.srcObject = stream;
  try { await video.play(); } catch (error) { stop(); throw failure('graded_scan_failed', error); }
  (async () => {
    while (!stopped) {
      if (video.readyState >= 2 && video.videoWidth) {
        const attempt = attempts[tick++ % attempts.length];
        let text = null;
        try {
          text = decoder.native && tick % 2 ? await decoder.detect(video)
            : await decoder.decode(frameFrom(video, attempt, canvas), attempt);
        } catch { text = null; }
        if (stopped) break;
        if (text) onText(text);
      }
      await wait(CAMERA_PAUSE_MS);
    }
  })();
  return {stop};
}
