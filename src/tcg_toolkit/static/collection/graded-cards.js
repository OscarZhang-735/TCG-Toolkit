// Graded cards: saved certificates plus the browser's queue of slow lookups.
// No DOM and no imports: graded-cards-ui.js renders the state, tests drive it directly.

export const MAX_CONCURRENT = 2;
// Extra wait beyond a provider's own timeout before the browser stops waiting.
const CLIENT_GRACE_MS = 30_000;
const DEFAULT_TIMEOUT_S = 225;

// Upstream codes are prefixed by provider ({provider}_{kind}); one message per kind keeps
// a new provider working without new translations.
const KIND_KEYS = {timeout: 'graded_error_timeout', rate_limited: 'graded_error_rate_limited',
  unavailable: 'graded_error_unavailable', unreadable: 'graded_error_unreadable',
  authentication_failed: 'graded_error_gateway', not_configured: 'graded_error_not_configured'};
const DETAIL_KEYS = {grading_certificate_not_found: 'graded_error_not_found', invalid_cert_number: 'graded_error_invalid_number',
  unsupported_grading_provider: 'graded_error_provider', grading_busy: 'graded_error_busy',
  graded_card_not_found: 'graded_error_missing'};
// Asking again cannot help these: the number or provider has to change.
const FINAL = new Set(['graded_error_not_found', 'graded_error_invalid_number', 'graded_error_provider']);
const KIND = /^[a-z0-9]+_(timeout|rate_limited|unavailable|unreadable|authentication_failed|not_configured)$/;
export const ERROR_KEYS = [...new Set([...Object.values(KIND_KEYS), ...Object.values(DETAIL_KEYS),
  'graded_error_network', 'graded_error_client_timeout', 'graded_error_failed'])];

// {key, retryable, code}: code is the server's detail (or HTTP status) shown as small print.
export function gradingError(detail, status = 0) {
  let key = typeof detail === 'string' ? DETAIL_KEYS[detail] ?? KIND_KEYS[KIND.exec(detail)?.[1]] : undefined;
  key ??= status ? 'graded_error_failed' : 'graded_error_network';
  return {key, retryable: !FINAL.has(key), code: typeof detail === 'string' ? detail : status ? `HTTP ${status}` : ''};
}
const clientTimeout = () => ({key: 'graded_error_client_timeout', retryable: true, code: ''});

// Sortable value of a grade label: its last number, half a step more for "+", and
// authentication-only (AU) below every numeric grade.
export function gradeValue(grade) {
  const text = String(grade ?? '').trim();
  const numbers = [...text.matchAll(/(\d+(?:\.\d+)?)(\+?)/g)];
  if (!numbers.length) return /^au/i.test(text) ? 0 : -1;
  const [, value, plus] = numbers.at(-1);
  return Number(value) + (plus ? 0.5 : 0);
}

export function formatElapsed(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds / 60) % 60;
  const pad = value => String(value).padStart(2, '0');
  return `${hours ? `${hours}:${pad(minutes)}` : minutes}:${pad(seconds % 60)}`;
}

const fold = value => String(value ?? '').toLowerCase();
// The user's own name for a card when set, else the certificate's card name ('' when neither).
export const displayName = row => row.display_name || row.title || '';
const ORDER = {
  newest: (a, b) => b.created_at - a.created_at,
  oldest: (a, b) => a.created_at - b.created_at,
  grade_desc: (a, b) => gradeValue(b.grade) - gradeValue(a.grade),
  grade_asc: (a, b) => gradeValue(a.grade) - gradeValue(b.grade),
  title: (a, b) => fold(displayName(a)).localeCompare(fold(displayName(b))),
};
export const SORTS = Object.keys(ORDER);
// Search covers the title, number, grade, provider and every certificate field, folded or not.
export function filterGraded(records, {query = '', hiddenProviders = new Set(), sort = 'newest'} = {}) {
  const needle = fold(query).trim();
  const rows = records.filter(row => !hiddenProviders.has(row.provider) && (!needle ||
    [row.display_name, row.title, row.cert_number, row.grade, row.provider, ...Object.values(row.fields ?? {}),
      ...Object.values(row.english_fields ?? {})]
      .some(value => fold(value).includes(needle))));
  return ORDER[sort] ? rows.sort(ORDER[sort]) : rows;  // stable: ties keep the server's order
}

export const pendingKey = (provider, certNumber) => `${provider}:${certNumber}`;

// Photo crops: corners are [[x, y] x4] in the uploaded image's pixels.
export const SIDES = ['front', 'back'];
// A small inset so the default frame's handles are visible and grabbable.
export function fullCorners(width, height, inset = 0.04) {
  const dx = width * inset, dy = height * inset;
  return [[dx, dy], [width - dx, dy], [width - dx, height - dy], [dx, height - dy]];
}
export function clampCorner([x, y], width, height) {
  return [Math.min(Math.max(x, 0), width), Math.min(Math.max(y, 0), height)];
}
export const cornersParam = corners => corners.flat().map(value => Math.round(value * 10) / 10).join(',');
// Photo errors: server codes are their own message keys; anything else is generic.
const IMAGE_ERRORS = new Set(['graded_image_invalid', 'graded_image_too_large', 'graded_image_dimensions',
  'graded_image_crop', 'graded_image_storage_failed', 'graded_card_not_found']);
export function imageError(detail, status = 0) {
  if (detail === 'graded_card_not_found') return 'graded_error_missing';
  if (IMAGE_ERRORS.has(detail)) return detail;
  if (status === 429) return 'graded_image_rate_limited';
  return status ? 'graded_image_failed' : 'graded_error_network';
}
export const IMAGE_ERROR_KEYS = ['graded_image_invalid', 'graded_image_too_large', 'graded_image_dimensions',
  'graded_image_crop', 'graded_image_storage_failed', 'graded_image_rate_limited', 'graded_image_failed'];

// {front, back}: images tagged with a side first, then untagged ones in page order.
export function slabImages(images = []) {
  const front = images.find(image => image.side === 'front'), back = images.find(image => image.side === 'back');
  const rest = images.filter(image => !image.side);
  return {front: front ?? rest.shift() ?? null, back: back ?? rest.shift() ?? null};
}

// Lets the server cancel a lookup this tab started; getRandomValues also works over plain HTTP.
const randomId = () => Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)),
  byte => byte.toString(16).padStart(2, '0')).join('');

// Group errors from the server, in the wording the shared group dialogs use.
const GROUP_ERRORS = {graded_group_name_taken: 'favorite_group_name_taken', graded_group_not_found: 'favorite_group_missing',
  graded_card_not_found: 'graded_error_missing'};

export function createGradedCards(onChange, {now = () => Date.now(), maxConcurrent = MAX_CONCURRENT,
  setTimer = (callback, ms) => setTimeout(callback, ms), clearTimer = id => clearTimeout(id), newId = randomId} = {}) {
  let request, generation = 0, controller = new AbortController(), running = 0;
  // Lookups in flight by job ('add:<key>' / 'refresh:<id>'): their request id and local stop.
  const inflight = new Map();
  // Adds and refreshes share one queue, so a tab never runs more than maxConcurrent slow
  // lookups at once (the server allows three per account).
  let queue = [];
  // groups as in the card library; group is the selected tab (null shows every card).
  const initial = () => ({providers: [], records: [], pending: [], refreshing: {}, removing: new Set(), imageBusy: new Set(),
    priceBusy: new Set(), nameBusy: new Set(), groups: [], group: null, groupBusy: false, moving: new Set(),
    ready: false, loading: false, error: '', highlight: '', notice: null});
  const state = initial();
  const visible = () => state.group === null ? state.records : state.records.filter(row => row.group_id === state.group);

  function clear() {
    generation++; controller.abort(); controller = new AbortController();
    queue = []; running = 0; inflight.clear();
    Object.assign(state, initial());
    onChange();
  }
  async function call(path, method, body, signal) {
    let response;
    try {
      response = await request('/api' + path, {method, cache: 'no-store', signal,
        ...(method === 'GET' ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body ?? {})})});
    } catch (error) {
      if (error?.name === 'AbortError') throw error;
      throw Object.assign(new Error('network'), {status: 0, cause: error});
    }
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) throw Object.assign(new Error('request failed'), {status: response.status, detail: data?.detail});
    return data;
  }
  // Abort on a timeout of the provider's limit plus some grace, or on an account change.
  function deadline(provider) {
    const local = new AbortController(), parent = controller, abort = () => local.abort();
    const seconds = state.providers.find(item => item.id === provider)?.timeout_seconds ?? DEFAULT_TIMEOUT_S;
    let timedOut = false;
    parent.signal.addEventListener('abort', abort, {once: true});
    const timer = setTimer(() => { timedOut = true; local.abort(); }, seconds * 1000 + CLIENT_GRACE_MS);
    return {signal: local.signal, timedOut: () => timedOut, stop: abort,
      dispose() { clearTimer(timer); parent.signal.removeEventListener('abort', abort); }};
  }
  async function load() {
    const current = generation;
    state.loading = true; state.error = ''; onChange();
    try {
      const [providers, cards, groups] = await Promise.all([call('/grading/providers', 'GET', null, controller.signal),
        call('/graded-cards', 'GET', null, controller.signal), call('/graded-cards/groups', 'GET', null, controller.signal)]);
      if (current !== generation) return;
      state.providers = providers.items; state.records = cards.items; state.groups = groups.items; state.ready = true;
      if (!state.groups.some(group => group.id === state.group)) state.group = null;
    } catch (error) {
      if (current === generation && error.name !== 'AbortError') state.error = 'graded_load_failed';
    } finally {
      if (current === generation) { state.loading = false; onChange(); }
    }
  }
  function pump() {
    while (running < maxConcurrent && queue.length) {
      const job = queue.shift(), current = generation;
      running++;
      run(job).finally(() => { if (current === generation) { running--; pump(); } });
    }
  }
  function enqueue(job) { queue.push(job); pump(); onChange(); }

  // Returns {status: 'queued' | 'pending' | 'exists', key?, id?} so the dialog can explain.
  // group_id: where the saved card goes (the add dialog defaults to the selected tab).
  function add({provider, cert_number, price, currency, group_id = null}) {
    const existing = state.records.find(row => row.provider === provider && row.cert_number === cert_number);
    if (existing) { state.highlight = existing.id; onChange(); return {status: 'exists', id: existing.id}; }
    const key = pendingKey(provider, cert_number), waiting = state.pending.find(item => item.key === key);
    if (waiting) {
      if (waiting.status === 'failed') {
        Object.assign(waiting, {price, currency, group_id});
        retry(key);
      }
      return {status: 'pending', key};
    }
    state.pending.unshift({key, provider, cert_number, price, currency, group_id, status: 'queued', startedAt: null, error: null});
    enqueue({kind: 'add', key});
    return {status: 'queued', key};
  }
  function retry(key) {
    const item = state.pending.find(entry => entry.key === key);
    if (!item || item.status !== 'failed') return;
    Object.assign(item, {status: 'queued', startedAt: null, error: null});
    enqueue({kind: 'add', key});
  }
  // Stops a lookup in flight here and on the server. If the server had already saved the
  // card, the list is reloaded so it appears anyway.
  function cancelInflight(job, provider, certNumber) {
    const entry = inflight.get(job);
    if (!entry) return;
    inflight.delete(job); entry.stop();
    const current = generation;
    call(`/graded-cards/lookups/${entry.requestId}`, 'DELETE', {}, controller.signal).then(() => {
      if (current !== generation) return;
      state.notice = {key: 'graded_cancelled_notice', provider, cert_number: certNumber}; onChange();
    }, async error => {
      if (current !== generation || error.name === 'AbortError') return;
      if (error.status === 409 && error.detail === 'graded_lookup_finished') {
        await load();
        if (current === generation) { state.notice = {key: 'graded_cancel_late_notice', provider, cert_number: certNumber}; onChange(); }
      }
      // Otherwise (offline) the server may still finish; the card then appears on the next load.
    });
  }
  // Takes a lookup off the list: queued ones never run, running ones are cancelled.
  function dismiss(key) {
    const item = state.pending.find(entry => entry.key === key);
    if (!item) return;
    if (item.status === 'loading') cancelInflight(`add:${key}`, item.provider, item.cert_number);
    state.pending = state.pending.filter(entry => entry !== item);
    queue = queue.filter(job => !(job.kind === 'add' && job.key === key));
    onChange();
  }
  function refresh(id) {
    const entry = state.refreshing[id];
    if (!state.records.some(row => row.id === id) || (entry && entry.status !== 'failed') || state.removing.has(id)) return;
    state.refreshing[id] = {status: 'queued', startedAt: null, error: null};
    enqueue({kind: 'refresh', id});
  }
  // Clears a failed refresh, or cancels one waiting or running (the saved card stays).
  function dismissRefresh(id) {
    const entry = state.refreshing[id];
    if (!entry) return;
    if (entry.status === 'loading') {
      const row = state.records.find(card => card.id === id);
      cancelInflight(`refresh:${id}`, row?.provider, row?.cert_number);
    }
    queue = queue.filter(job => !(job.kind === 'refresh' && job.id === id));
    delete state.refreshing[id]; onChange();
  }
  // Position among queued jobs (0 = next to start), or -1 when not queued.
  const queuePosition = (kind, value) => queue.findIndex(job => job.kind === kind && (job.key ?? job.id) === value);

  async function run(job) {
    if (job.kind === 'add') return runAdd(job.key);
    return runRefresh(job.id);
  }
  async function runAdd(key) {
    const current = generation, item = state.pending.find(entry => entry.key === key);
    if (!item || item.status !== 'queued') return;
    Object.assign(item, {status: 'loading', startedAt: now(), error: null}); onChange();
    const limit = deadline(item.provider), job = `add:${key}`, lookup = {requestId: newId(), stop: limit.stop};
    inflight.set(job, lookup);
    try {
      const card = await call('/graded-cards', 'POST', {provider: item.provider, cert_number: item.cert_number,
        request_id: lookup.requestId, price: item.price, currency: item.currency,
        group_id: state.groups.some(group => group.id === item.group_id) ? item.group_id : null}, limit.signal);
      if (current !== generation) return;
      state.pending = state.pending.filter(entry => entry !== item);
      state.records = [card, ...state.records.filter(row => row.id !== card.id)];
      state.notice = {key: 'graded_added_notice', provider: card.provider, cert_number: card.cert_number};
    } catch (error) {
      if (current !== generation) return;
      if (error.status === 409 && error.detail?.type === 'graded_card_duplicate') return await duplicate(item, error.detail.id, current);
      if (error.status === 404 && error.detail === 'graded_group_not_found') {
        // The group went away (another tab) before the lookup started: save it ungrouped.
        state.groups = state.groups.filter(group => group.id !== item.group_id);
        Object.assign(item, {status: 'queued', startedAt: null, error: null, group_id: null});
        enqueue({kind: 'add', key: item.key});
        return;
      }
      if (error.name === 'AbortError' && !limit.timedOut()) return;
      if (error.detail === 'grading_lookup_cancelled') {  // cancelled from another tab
        state.pending = state.pending.filter(entry => entry !== item);
        return;
      }
      Object.assign(item, {status: 'failed', error: limit.timedOut() ? clientTimeout() : gradingError(error.detail, error.status)});
      state.notice = {key: 'graded_failed_notice', provider: item.provider, cert_number: item.cert_number};
    } finally {
      limit.dispose();
      if (inflight.get(job) === lookup) inflight.delete(job);
      if (current === generation) onChange();
    }
  }
  // Already saved, possibly by an earlier try the browser stopped waiting for: reload if
  // needed and point at the saved card instead of failing.
  async function duplicate(item, id, current) {
    state.pending = state.pending.filter(entry => entry !== item);
    if (!state.records.some(row => row.id === id)) await load();
    if (current !== generation) return;
    state.highlight = id;
    state.notice = {key: 'graded_duplicate_notice', provider: item.provider, cert_number: item.cert_number};
  }
  async function runRefresh(id) {
    const current = generation, entry = state.refreshing[id], row = state.records.find(card => card.id === id);
    if (!entry || entry.status !== 'queued') return;
    if (!row) { delete state.refreshing[id]; return; }
    Object.assign(entry, {status: 'loading', startedAt: now(), error: null}); onChange();
    const limit = deadline(row.provider), job = `refresh:${id}`, lookup = {requestId: newId(), stop: limit.stop};
    inflight.set(job, lookup);
    try {
      const card = await call(`/graded-cards/${encodeURIComponent(id)}/refresh`, 'POST', {request_id: lookup.requestId}, limit.signal);
      if (current !== generation) return;
      // A price save or a move can finish while this lookup is in flight.
      state.records = state.records.map(saved => saved.id === id
        ? {...card, price: saved.price, currency: saved.currency, group_id: saved.group_id, display_name: saved.display_name} : saved);
      delete state.refreshing[id];
      state.notice = {key: 'graded_refreshed_notice', provider: card.provider, cert_number: card.cert_number};
    } catch (error) {
      if (current !== generation) return;
      if (error.name === 'AbortError' && !limit.timedOut()) return;
      if (error.detail === 'grading_lookup_cancelled') { delete state.refreshing[id]; return; }
      if (error.status === 404 && error.detail === 'graded_card_not_found') {
        // Deleted in another tab: drop it here too.
        state.records = state.records.filter(saved => saved.id !== id);
        delete state.refreshing[id];
        return;
      }
      Object.assign(entry, {status: 'failed', error: limit.timedOut() ? clientTimeout() : gradingError(error.detail, error.status)});
    } finally {
      limit.dispose();
      if (inflight.get(job) === lookup) inflight.delete(job);
      if (current === generation) onChange();
    }
  }
  async function setPrice(id, price) {
    const current = generation;
    if (state.priceBusy.has(id)) return '';
    state.priceBusy.add(id); onChange();
    try {
      const savedPrice = await call(`/graded-cards/${encodeURIComponent(id)}/price`, 'PUT', price, controller.signal);
      if (current !== generation) return '';
      state.records = state.records.map(row => row.id === id ? {...row, ...savedPrice} : row);
      return '';
    } catch (error) {
      if (current !== generation || error.name === 'AbortError') return '';
      if (error.status === 404) state.records = state.records.filter(row => row.id !== id);
      return 'graded_price_failed';
    } finally {
      if (current === generation) { state.priceBusy.delete(id); onChange(); }
    }
  }
  // Groups: writes resolve to a translation key ('' on success) for the open dialog.
  async function editGroup(operation) {
    const current = generation;
    if (!state.ready || state.groupBusy) return 'graded_group_failed';
    state.groupBusy = true; onChange();
    try {
      await operation(current);
      return '';
    } catch (error) {
      return groupFailure(error, current);
    } finally {
      if (current === generation) { state.groupBusy = false; onChange(); }
    }
  }
  // Another tab may have deleted the group or card; reload so the page matches the server.
  function groupFailure(error, current) {
    if (current !== generation || error.name === 'AbortError') return '';
    const key = GROUP_ERRORS[error.detail] ?? 'graded_group_failed';
    if (error.status === 404) load();
    return key;
  }
  const createGroup = name => editGroup(async current => {
    const group = await call('/graded-cards/groups', 'POST', {name}, controller.signal);
    if (current === generation) { state.groups.push(group); state.group = group.id; }
  });
  const renameGroup = (id, name) => editGroup(async current => {
    const group = await call(`/graded-cards/groups/${encodeURIComponent(id)}`, 'PUT', {name}, controller.signal);
    if (current === generation) state.groups = state.groups.map(row => row.id === id ? group : row);
  });
  // Deleting a group keeps its cards; the server leaves them ungrouped.
  const deleteGroup = id => editGroup(async current => {
    await call(`/graded-cards/groups/${encodeURIComponent(id)}`, 'DELETE', {}, controller.signal);
    if (current !== generation) return;
    state.groups = state.groups.filter(group => group.id !== id);
    state.records = state.records.map(row => row.group_id === id ? {...row, group_id: null} : row);
    if (state.group === id) state.group = null;
  });
  function select(id) {
    if (id !== null && !state.groups.some(group => group.id === id)) return;
    state.group = id; onChange();
  }
  // groupId null takes the card out of its group; resolves to an error key ('' on success).
  async function move(id, groupId) {
    const current = generation, row = state.records.find(card => card.id === id);
    if (!state.ready || !row || state.moving.has(id)) return 'graded_group_failed';
    if (row.group_id === groupId) return '';
    state.moving.add(id); onChange();
    try {
      const card = await call(`/graded-cards/${encodeURIComponent(id)}/group`, 'PUT', {group_id: groupId}, controller.signal);
      if (current === generation) state.records = state.records.map(saved => saved.id === id ? {...saved, group_id: card.group_id} : saved);
      return '';
    } catch (error) {
      return groupFailure(error, current);
    } finally {
      if (current === generation) { state.moving.delete(id); onChange(); }
    }
  }
  // name: the user's display name ('' clears it). Resolves to '' on success or an error key.
  async function setDisplayName(id, name) {
    const current = generation;
    if (state.nameBusy.has(id)) return '';
    state.nameBusy.add(id); onChange();
    try {
      const saved = await call(`/graded-cards/${encodeURIComponent(id)}/display-name`, 'PUT', {display_name: name}, controller.signal);
      if (current !== generation) return '';
      state.records = state.records.map(row => row.id === id ? {...row, display_name: saved.display_name} : row);
      return '';
    } catch (error) {
      if (current !== generation || error.name === 'AbortError') return '';
      if (error.status === 404) { state.records = state.records.filter(row => row.id !== id); return 'graded_error_missing'; }
      return 'graded_display_name_failed';
    } finally {
      if (current === generation) { state.nameBusy.delete(id); onChange(); }
    }
  }
  // Resolves to '' on success or an error key.
  async function remove(id) {
    const current = generation;
    if (!state.records.some(row => row.id === id) || state.removing.has(id)
      || state.refreshing[id]?.status === 'loading') return 'graded_delete_failed';
    state.removing.add(id);
    queue = queue.filter(job => !(job.kind === 'refresh' && job.id === id));
    delete state.refreshing[id];
    onChange();
    try {
      await call(`/graded-cards/${encodeURIComponent(id)}`, 'DELETE', {}, controller.signal);
      if (current === generation) state.records = state.records.filter(row => row.id !== id);
      return '';
    } catch (error) {
      if (current !== generation || error.name === 'AbortError') return '';
      if (error.status === 404) { state.records = state.records.filter(row => row.id !== id); return ''; }
      return 'graded_delete_failed';
    } finally {
      if (current === generation) { state.removing.delete(id); onChange(); }
    }
  }
  // Photos go up as raw image bodies (detection, then the confirmed crop).
  async function sendImage(path, method, blob) {
    let response;
    try {
      response = await request('/api' + path, {method, cache: 'no-store', signal: controller.signal,
        headers: {'Content-Type': blob.type || 'image/jpeg'}, body: blob});
    } catch (error) {
      if (error?.name === 'AbortError') throw error;
      throw Object.assign(new Error('network'), {key: imageError(undefined, 0)});
    }
    const data = await response.json().catch(() => null);
    if (!response.ok) throw Object.assign(new Error('image request failed'), {key: imageError(data?.detail, response.status)});
    return data;
  }
  // Suggested crop for a photo: {width, height, corners | null}. Rejects with {key}.
  const detectCard = blob => sendImage('/graded-cards/detect-card', 'POST', blob);
  // Resolves to '' on success or an error key; the card's record is replaced.
  async function changeImage(id, side, run) {
    const current = generation, busy = `${id}:${side}`;
    if (!state.records.some(row => row.id === id) || state.imageBusy.has(busy)) return 'graded_image_failed';
    state.imageBusy.add(busy); onChange();
    try {
      const card = await run();
      if (current !== generation) return '';
      state.records = state.records.map(row => row.id === id ? {...row, images: card.images} : row);
      return '';
    } catch (error) {
      if (current !== generation || error.name === 'AbortError') return '';
      if (error.status === 404 || error.key === 'graded_error_missing') state.records = state.records.filter(row => row.id !== id);
      return error.key ?? imageError(error.detail, error.status);
    } finally {
      if (current === generation) { state.imageBusy.delete(busy); onChange(); }
    }
  }
  // Optional brightness calibration (glare, uneven light): resolves to a same-size JPEG.
  // Nothing is stored; the caller keeps the original to undo it. Rejects with {key}.
  async function calibrateImage(blob) {
    let response;
    try {
      response = await request('/api/graded-cards/calibrate-image', {method: 'POST', cache: 'no-store',
        signal: controller.signal, headers: {'Content-Type': blob.type || 'image/jpeg'}, body: blob});
    } catch (error) {
      if (error?.name === 'AbortError') throw error;
      throw Object.assign(new Error('network'), {key: imageError(undefined, 0)});
    }
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw Object.assign(new Error('calibration failed'), {key: imageError(data?.detail, response.status)});
    }
    return response.blob();
  }
  const uploadImage = (id, side, blob, corners) => changeImage(id, side, () =>
    sendImage(`/graded-cards/${encodeURIComponent(id)}/images/${side}?corners=${cornersParam(corners)}`, 'PUT', blob));
  const restoreImage = (id, side) => changeImage(id, side, () =>
    call(`/graded-cards/${encodeURIComponent(id)}/images/${side}`, 'DELETE', {}, controller.signal));
  const providerName = id => state.providers.find(item => item.id === id)?.name ?? String(id).toUpperCase();
  const timeoutSeconds = id => state.providers.find(item => item.id === id)?.timeout_seconds ?? DEFAULT_TIMEOUT_S;

  return {state, visible, clear, load, add, retry, dismiss, refresh, dismissRefresh, remove, queuePosition, providerName,
    createGroup, renameGroup, deleteGroup, select, move,
    setPrice, setDisplayName, detectCard, calibrateImage, uploadImage, restoreImage,
    timeoutSeconds, busy: () => state.pending.some(item => item.status !== 'failed')
      || Object.values(state.refreshing).some(entry => entry.status !== 'failed'),
    activate(api) { clear(); request = api; return load(); }};
}
