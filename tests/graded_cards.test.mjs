import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
// Browsers (and Node 19+) provide crypto globally; request ids use getRandomValues.
globalThis.crypto ??= webcrypto;
const base = new URL('../src/tcg_toolkit/static/', import.meta.url);
const asModule = value => `data:text/javascript;base64,${Buffer.from(value).toString('base64')}`;
const {createGradedCards, gradingError, gradeValue, formatElapsed, filterGraded, slabImages, ERROR_KEYS, MAX_CONCURRENT,
  fullCorners, clampCorner, cornersParam, imageError, IMAGE_ERROR_KEYS} =
  await import(asModule(await readFile(new URL('collection/graded-cards.js', base), 'utf8')));
const {messages} = await import(asModule(await readFile(new URL('locales.js', base), 'utf8')));

const reply = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const PROVIDERS = [{id: 'psa', name: 'PSA', timeout_seconds: 225, scan_patterns: []},
  {id: 'ars', name: 'ARS', timeout_seconds: 40, scan_patterns: []}];
const card = (id, provider, cert, extra = {}) => ({id, provider, cert_number: cert, source_url: `https://example.test/${cert}`,
  title: `Card ${id}`, grade: '10', fields: {}, population: {}, images: [], created_at: 1, refreshed_at: 1, group_id: null, display_name: null, ...extra});

// Lists answer at once; lookups (POST) wait in `waiting` until the test settles them, and
// reject with AbortError when their signal aborts, like fetch.
function server({records = [], providers = PROVIDERS, groups = []} = {}) {
  const calls = [], waiting = [];
  let deleteReply = null;
  const api = (url, options = {}) => {
    const {pathname} = new URL(url, 'http://local'), method = options.method ?? 'GET';
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({method, path: pathname, body});
    if (method === 'GET' && pathname === '/api/grading/providers') return Promise.resolve(reply({items: providers}));
    if (method === 'GET' && pathname === '/api/graded-cards') return Promise.resolve(reply({items: records}));
    if (method === 'GET' && pathname === '/api/graded-cards/groups') return Promise.resolve(reply({items: groups}));
    // Group writes answer at once, like the lists.
    if (pathname.startsWith('/api/graded-cards/groups')) {
      const id = pathname.split('/')[4];
      if (method === 'POST') {
        if (groups.some(group => group.name === body.name)) return Promise.resolve(reply({detail: 'graded_group_name_taken'}, 409));
        const group = {id: `g${groups.length + 1}`, name: body.name, created_at: 1}; groups = [...groups, group];
        return Promise.resolve(reply(group, 201));
      }
      if (!groups.some(group => group.id === id)) return Promise.resolve(reply({detail: 'graded_group_not_found'}, 404));
      if (method === 'PUT') { groups = groups.map(group => group.id === id ? {...group, name: body.name} : group); return Promise.resolve(reply(groups.find(group => group.id === id))); }
      groups = groups.filter(group => group.id !== id); records = records.map(row => row.group_id === id ? {...row, group_id: null} : row);
      return Promise.resolve(reply(null, 204));
    }
    if (method === 'PUT' && pathname.endsWith('/group')) {
      if (body.group_id !== null && !groups.some(group => group.id === body.group_id)) return Promise.resolve(reply({detail: 'graded_group_not_found'}, 404));
      const id = pathname.split('/')[3], row = records.find(card => card.id === id);
      return Promise.resolve(reply({...row, group_id: body.group_id}));
    }
    if (method === 'DELETE') return Promise.resolve(deleteReply ?? reply(null, 204));
    let resolve, reject;
    const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
    options.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), {once: true});
    waiting.push({path: pathname, body, resolve, reject});
    return promise;
  };
  return {api, calls, waiting, set records(value) { records = value; }, set deleteReply(value) { deleteReply = value; }};
}
// Safety timers stay inert unless a test drives them: real ones (up to 255 s) would keep
// the test process alive after lookups left waiting.
async function ready(options = {}, init = {}) {
  const backend = server(init), changes = [];
  const graded = createGradedCards(() => changes.push(1), {setTimer: () => 0, clearTimer: () => {}, ...options});
  await graded.activate(backend.api);
  return {graded, backend, changes};
}

test('error details map to localized messages and distinguish immediately retryable errors', () => {
  assert.deepEqual(gradingError('psa_timeout', 504), {key: 'graded_error_timeout', retryable: true, code: 'psa_timeout'});
  assert.equal(gradingError('cgc_rate_limited', 502).key, 'graded_error_rate_limited');
  // A provider added later needs no new strings.
  assert.equal(gradingError('bgs_unreadable', 502).key, 'graded_error_unreadable');
  assert.equal(gradingError('ars_authentication_failed', 502).key, 'graded_error_gateway');
  assert.equal(gradingError('psa_not_configured', 503).key, 'graded_error_not_configured');
  for (const detail of ['grading_certificate_not_found', 'invalid_cert_number', 'unsupported_grading_provider']) {
    assert.equal(gradingError(detail, 404).retryable, false, detail);
  }
  assert.deepEqual(gradingError('grading_busy', 429), {key: 'graded_error_busy', retryable: true, code: 'grading_busy'});
  assert.deepEqual(gradingError('grading_daily_limit', 429), {key: 'graded_error_daily_limit', retryable: false, code: 'grading_daily_limit'});
  assert.deepEqual(gradingError(undefined, 0), {key: 'graded_error_network', retryable: true, code: ''});
  assert.deepEqual(gradingError([{msg: 'bad'}], 500), {key: 'graded_error_failed', retryable: true, code: 'HTTP 500'});
  assert.equal(gradingError('something_new_but_unknown', 502).key, 'graded_error_failed');
  for (const key of ERROR_KEYS) {
    assert.ok(messages.en[key] && messages['zh-CN'][key], key);
  }
});

test('UI string keys used by the graded-card modules exist in both languages', async () => {
  const sources = await Promise.all(['collection/graded-cards.js', 'collection/graded-cards-ui.js', 'collection/grading-scan.js']
    .map(name => readFile(new URL(name, base), 'utf8')));
  // Server detail codes share the prefix but are not messages.
  const codes = new Set(['graded_card_duplicate', 'graded_card_not_found', 'graded_lookup_finished',
    'graded_group_name_taken', 'graded_group_not_found']);
  const keys = new Set(sources.join('\n').match(/'graded_[a-z_]+'/g).map(key => key.slice(1, -1)).filter(key => !codes.has(key)));
  assert.ok(keys.size > 50);
  for (const key of keys) assert.ok(messages.en[key] && messages['zh-CN'][key], key);
});

test('grades sort by their last number, "+" above the plain grade and AU at the bottom', () => {
  assert.equal(gradeValue('GEM MT 10'), 10);
  assert.equal(gradeValue('PRISTINE 10'), 10);
  assert.equal(gradeValue('10+'), 10.5);
  assert.equal(gradeValue('NM-MT 8.5'), 8.5);
  assert.equal(gradeValue('AU'), 0);
  assert.equal(gradeValue(''), -1);
  assert.ok(gradeValue('MINT 9') < gradeValue('10') && gradeValue('AU') < gradeValue('1'));
});

test('elapsed time reads m:ss, with hours once it gets that long', () => {
  assert.equal(formatElapsed(0), '0:00');
  assert.equal(formatElapsed(65_400), '1:05');
  assert.equal(formatElapsed(3_725_000), '1:02:05');
  assert.equal(formatElapsed(-5), '0:00');
});

test('search covers fields and numbers, providers can be hidden, and sorts are stable', () => {
  const rows = [card('a', 'psa', '111', {title: 'Mayu', grade: 'GEM MT 10', created_at: 3, fields: {Year: '2024'}}),
    card('b', 'ars', '047', {title: 'alpha', grade: '10+', created_at: 2}),
    card('c', 'psa', '222', {title: 'Beta', grade: 'MINT 9', created_at: 1}),
    card('d', 'psa', '333', {title: 'gamma', grade: 'GEM MT 10', created_at: 1})];
  const ids = options => filterGraded(rows, options).map(row => row.id);
  assert.deepEqual(ids({}), ['a', 'b', 'c', 'd']);
  assert.deepEqual(ids({query: '2024'}), ['a']);
  assert.deepEqual(ids({query: ' 04 '}), ['b']);
  assert.deepEqual(ids({query: 'gem mt'}), ['a', 'd']);
  assert.deepEqual(ids({query: 'MINT'}), ['c']);
  assert.deepEqual(ids({hiddenProviders: new Set(['psa'])}), ['b']);
  assert.deepEqual(ids({sort: 'oldest'}), ['c', 'd', 'b', 'a']);
  assert.deepEqual(ids({sort: 'grade_desc'}), ['b', 'a', 'd', 'c']);
  assert.deepEqual(ids({sort: 'grade_asc'}), ['c', 'a', 'd', 'b']);
  assert.deepEqual(ids({sort: 'title'}), ['b', 'c', 'd', 'a']);
  assert.deepEqual(ids({sort: 'not-a-sort'}), ['a', 'b', 'c', 'd']);
  assert.deepEqual(rows.map(row => row.id), ['a', 'b', 'c', 'd'], 'the saved list itself is not reordered');
});

test('front and back images come from side tags, then from untagged images in order', () => {
  const front = {url: 'f', side: 'front'}, back = {url: 'b', side: 'back'}, first = {url: '1', side: null}, second = {url: '2'};
  assert.deepEqual(slabImages([back, front]), {front, back});
  assert.deepEqual(slabImages([first, second]), {front: first, back: second});
  assert.deepEqual(slabImages([front, first]), {front, back: first});
  assert.deepEqual(slabImages([]), {front: null, back: null});
});

test('activation loads providers and saved cards', async () => {
  const records = [card('one', 'psa', '98428602')];
  const {graded, backend} = await ready({}, {records});
  assert.equal(graded.state.ready, true);
  assert.deepEqual(graded.state.providers, PROVIDERS);
  assert.deepEqual(graded.state.records, records);
  assert.deepEqual(backend.calls.map(call => call.path).sort(), ['/api/graded-cards', '/api/graded-cards/groups', '/api/grading/providers']);
  assert.equal(graded.providerName('psa'), 'PSA');
  assert.equal(graded.providerName('bgs'), 'BGS');
  assert.equal(graded.timeoutSeconds('ars'), 40);
});

test('a failed load reports an error that a retry clears', async () => {
  const backend = server();
  let fail = true;
  const graded = createGradedCards(() => {});
  await graded.activate((url, options) => fail ? Promise.resolve(reply({detail: 'x'}, 500)) : backend.api(url, options));
  assert.equal(graded.state.error, 'graded_load_failed');
  assert.equal(graded.state.ready, false);
  fail = false;
  await graded.load();
  assert.equal(graded.state.error, '');
  assert.equal(graded.state.ready, true);
});

test(`lookups run at most ${MAX_CONCURRENT} at a time; the rest wait their turn`, async () => {
  let clock = 1000;
  const {graded, backend} = await ready({now: () => clock});
  assert.equal(graded.add({provider: 'psa', cert_number: '1'}).status, 'queued');
  graded.add({provider: 'psa', cert_number: '2'});
  graded.add({provider: 'ars', cert_number: '3'});
  const status = () => Object.fromEntries(graded.state.pending.map(item => [item.cert_number, item.status]));
  assert.deepEqual(status(), {1: 'loading', 2: 'loading', 3: 'queued'});
  assert.equal(graded.state.pending[0].cert_number, '3', 'newest placeholder first');
  assert.equal(graded.state.pending.find(item => item.cert_number === '1').startedAt, 1000);
  assert.equal(graded.queuePosition('add', 'ars:3'), 0);
  assert.equal(graded.busy(), true);
  assert.deepEqual(backend.waiting.map(({body: {provider, cert_number}}) => ({provider, cert_number})),
    [{provider: 'psa', cert_number: '1'}, {provider: 'psa', cert_number: '2'}]);
  assert.match(backend.waiting[0].body.request_id, /^[0-9a-f]{32}$/);
  assert.notEqual(backend.waiting[0].body.request_id, backend.waiting[1].body.request_id);
  clock = 5000;
  backend.waiting[0].resolve(reply(card('c1', 'psa', '1'), 201));
  await settle();
  assert.deepEqual(status(), {2: 'loading', 3: 'loading'});
  assert.equal(graded.state.pending.find(item => item.cert_number === '3').startedAt, 5000);
  assert.deepEqual(graded.state.records.map(row => row.id), ['c1']);
  assert.deepEqual(graded.state.notice, {key: 'graded_added_notice', provider: 'psa', cert_number: '1'});
  backend.waiting[1].resolve(reply(card('c2', 'psa', '2'), 201));
  backend.waiting[2].resolve(reply(card('c3', 'ars', '3'), 201));
  await settle();
  assert.deepEqual(graded.state.pending, []);
  assert.deepEqual(graded.state.records.map(row => row.id), ['c3', 'c2', 'c1']);
  assert.equal(graded.busy(), false);
});

test('saved or in-flight certificates are not queued twice', async t => {
  const {graded, backend} = await ready({}, {records: [card('saved', 'psa', '9')]});
  t.after(() => graded.clear());
  assert.deepEqual(graded.add({provider: 'psa', cert_number: '9'}), {status: 'exists', id: 'saved'});
  assert.equal(graded.state.highlight, 'saved');
  graded.add({provider: 'ars', cert_number: '9'});
  assert.deepEqual(graded.add({provider: 'ars', cert_number: '9'}), {status: 'pending', key: 'ars:9'});
  assert.equal(backend.waiting.length, 1, 'same number at another provider is a different certificate');
});

test('failures keep the placeholder with a classified error; retry and edit paths', async () => {
  const {graded, backend} = await ready();
  graded.add({provider: 'psa', cert_number: '1'});
  graded.add({provider: 'psa', cert_number: '404'});
  backend.waiting[0].resolve(reply({detail: 'psa_timeout'}, 504));
  backend.waiting[1].resolve(reply({detail: 'grading_certificate_not_found'}, 404));
  await settle();
  const item = number => graded.state.pending.find(entry => entry.cert_number === number);
  assert.equal(item('1').status, 'failed');
  assert.deepEqual(item('1').error, {key: 'graded_error_timeout', retryable: true, code: 'psa_timeout'});
  assert.equal(item('404').error.retryable, false);
  assert.deepEqual(graded.state.notice, {key: 'graded_failed_notice', provider: 'psa', cert_number: '404'});
  graded.retry('psa:1');
  assert.equal(item('1').status, 'loading');
  backend.waiting[2].resolve(reply(card('ok', 'psa', '1'), 201));
  await settle();
  assert.equal(item('1'), undefined);
  // Adding a failed certificate again is a retry, not a second placeholder.
  assert.equal(graded.add({provider: 'psa', cert_number: '404'}).status, 'pending');
  assert.equal(item('404').status, 'loading');
  assert.equal(graded.state.pending.length, 1);
  backend.waiting[3].resolve(reply({detail: 'grading_certificate_not_found'}, 404));
  await settle();
  graded.dismiss('psa:404');
  assert.deepEqual(graded.state.pending, []);
});

test('cancelling a running lookup stops it here and on the server', async () => {
  let ids = 0;
  const {graded, backend} = await ready({maxConcurrent: 1, newId: () => `request-${++ids}`});
  graded.add({provider: 'psa', cert_number: '1'});
  graded.add({provider: 'psa', cert_number: '2'});
  assert.equal(backend.waiting[0].body.request_id, 'request-1');
  graded.dismiss('psa:1');
  await settle();
  assert.deepEqual(graded.state.pending.map(item => [item.cert_number, item.status]), [['2', 'loading']],
    'the slot goes to the next lookup');
  assert.deepEqual(backend.calls.filter(call => call.method === 'DELETE').map(call => call.path), ['/api/graded-cards/lookups/request-1']);
  assert.deepEqual(graded.state.notice, {key: 'graded_cancelled_notice', provider: 'psa', cert_number: '1'});
  // A late answer from the cancelled request changes nothing.
  backend.waiting[0].resolve(reply(card('late', 'psa', '1'), 201));
  await settle();
  assert.deepEqual(graded.state.records, []);
});

test('a cancel that arrives after the save reloads the list so the card still appears', async () => {
  const {graded, backend} = await ready({newId: () => 'request-1'});
  graded.add({provider: 'ars', cert_number: '5'});
  backend.deleteReply = reply({detail: 'graded_lookup_finished'}, 409);
  backend.records = [card('saved', 'ars', '5')];
  graded.dismiss('ars:5');
  await settle();
  assert.deepEqual(graded.state.records.map(row => row.id), ['saved']);
  assert.equal(graded.state.notice.key, 'graded_cancel_late_notice');
});

test('a running refresh can be cancelled and keeps the saved card', async () => {
  const saved = card('s', 'psa', '1');
  const {graded, backend} = await ready({newId: () => 'request-9'}, {records: [saved]});
  graded.refresh('s');
  assert.deepEqual(backend.waiting[0].body, {request_id: 'request-9'});
  graded.dismissRefresh('s');
  await settle();
  assert.equal(graded.state.refreshing.s, undefined);
  assert.deepEqual(graded.state.records, [saved]);
  assert.ok(backend.calls.some(call => call.method === 'DELETE' && call.path === '/api/graded-cards/lookups/request-9'));
  // Cancelled from another tab: the lookup answers 409 and the placeholder just goes away.
  graded.add({provider: 'psa', cert_number: '2'});
  backend.waiting[1].resolve(reply({detail: 'grading_lookup_cancelled'}, 409));
  await settle();
  assert.deepEqual(graded.state.pending, []);
  assert.equal(graded.state.notice?.key, 'graded_cancelled_notice');
});

test('folded English fields are searchable too', () => {
  const rows = [card('a', 'ars', '1', {title: '学生', fields: {'カード名称': '学生'}, english_fields: {'Card Name': 'Student'}})];
  assert.deepEqual(filterGraded(rows, {query: 'student'}).map(row => row.id), ['a']);
});

test('network errors are retryable and dismissing a queued lookup means it never runs', async () => {
  const {graded, backend} = await ready({maxConcurrent: 1});
  graded.add({provider: 'psa', cert_number: '1'});
  graded.add({provider: 'psa', cert_number: '2'});
  graded.dismiss('psa:2');
  backend.waiting[0].reject(new TypeError('Failed to fetch'));
  await settle();
  assert.deepEqual(graded.state.pending.map(item => [item.cert_number, item.status, item.error.key]),
    [['1', 'failed', 'graded_error_network']]);
  assert.equal(backend.waiting.length, 1);
});

test('a duplicate answer points at the saved card, reloading when this tab has not seen it', async () => {
  const {graded, backend} = await ready();
  graded.add({provider: 'psa', cert_number: '7'});
  // Saved meanwhile, e.g. by an earlier try the browser stopped waiting for.
  backend.records = [card('elsewhere', 'psa', '7')];
  backend.waiting[0].resolve(reply({detail: {type: 'graded_card_duplicate', id: 'elsewhere'}}, 409));
  await settle();
  assert.deepEqual(graded.state.pending, []);
  assert.deepEqual(graded.state.records.map(row => row.id), ['elsewhere']);
  assert.equal(graded.state.highlight, 'elsewhere');
  assert.equal(graded.state.notice.key, 'graded_duplicate_notice');
  assert.equal(backend.calls.filter(call => call.path === '/api/graded-cards' && call.method === 'GET').length, 2);
});

test('the browser stops waiting after the provider limit plus grace, as a retryable error', async () => {
  const timers = [];
  const {graded, backend} = await ready({setTimer: (callback, ms) => timers.push({callback, ms}) - 1, clearTimer: () => {}});
  graded.add({provider: 'ars', cert_number: '5'});
  assert.equal(timers[0].ms, 40_000 + 30_000);
  timers[0].callback();
  await settle();
  const [item] = graded.state.pending;
  assert.equal(item.status, 'failed');
  assert.deepEqual(item.error, {key: 'graded_error_client_timeout', retryable: true, code: ''});
  assert.equal(backend.waiting.length, 1);
});

test('switching accounts aborts lookups and drops their late answers', async t => {
  const first = server(), graded = createGradedCards(() => {});
  t.after(() => graded.clear());
  await graded.activate(first.api);
  graded.add({provider: 'psa', cert_number: '1'});
  const late = first.waiting[0];
  const second = server({records: [card('mine', 'ars', '2')]});
  await graded.activate(second.api);
  late.resolve(reply(card('theirs', 'psa', '1'), 201));
  await settle();
  assert.deepEqual(graded.state.pending, []);
  assert.deepEqual(graded.state.records.map(row => row.id), ['mine']);
  graded.add({provider: 'ars', cert_number: '3'});
  assert.equal(graded.state.pending[0].status, 'loading', 'the old account does not hold a slot');
});

test('refresh shares the queue, replaces the card on success and keeps it on failure', async () => {
  const saved = card('s', 'psa', '1', {grade: 'MINT 9', refreshed_at: 1});
  const {graded, backend} = await ready({maxConcurrent: 1}, {records: [saved, card('t', 'psa', '2')]});
  graded.refresh('s');
  graded.add({provider: 'ars', cert_number: '3'});
  assert.equal(graded.state.refreshing.s.status, 'loading');
  assert.equal(graded.state.pending[0].status, 'queued');
  assert.equal(backend.waiting[0].path, '/api/graded-cards/s/refresh');
  graded.refresh('s');
  assert.equal(backend.waiting.length, 1, 'a refresh in progress is not requested twice');
  backend.waiting[0].resolve(reply({...saved, grade: 'GEM MT 10', refreshed_at: 2}));
  await settle();
  assert.equal(graded.state.records[0].grade, 'GEM MT 10');
  assert.equal(graded.state.refreshing.s, undefined);
  assert.equal(graded.state.notice.key, 'graded_refreshed_notice');
  backend.waiting[1].resolve(reply(card('n', 'ars', '3'), 201));
  await settle();
  graded.refresh('t');
  backend.waiting[2].resolve(reply({detail: 'psa_unavailable'}, 502));
  await settle();
  assert.equal(graded.state.refreshing.t.status, 'failed');
  assert.equal(graded.state.refreshing.t.error.key, 'graded_error_unavailable');
  assert.equal(graded.state.records.find(row => row.id === 't').grade, '10', 'saved data stays');
  graded.dismissRefresh('t');
  assert.equal(graded.state.refreshing.t, undefined);
});

test('a late certificate refresh keeps the edited price', async () => {
  const saved = card('s', 'psa', '1', {price: 120, currency: 'JPY'});
  const {graded, backend} = await ready({}, {records: [saved]});
  graded.refresh('s');
  const saving = graded.setPrice('s', {price: '12.34', currency: 'CNY'});
  assert.deepEqual(backend.calls.at(-1), {method: 'PUT', path: '/api/graded-cards/s/price',
    body: {price: '12.34', currency: 'CNY'}});
  backend.waiting[1].resolve(reply({price: 12.34, currency: 'CNY'}));
  assert.equal(await saving, '');
  backend.waiting[0].resolve(reply({...saved, grade: '9'}));
  await settle();
  assert.deepEqual(graded.state.records[0], {...saved, grade: '9', price: 12.34, currency: 'CNY'});
});

test('a queued refresh can be cancelled; a card deleted elsewhere disappears on refresh', async () => {
  const {graded, backend} = await ready({maxConcurrent: 1}, {records: [card('a', 'psa', '1'), card('b', 'psa', '2')]});
  graded.refresh('a');
  graded.refresh('b');
  graded.dismissRefresh('b');
  assert.equal(graded.state.refreshing.b, undefined);
  backend.waiting[0].resolve(reply({detail: 'graded_card_not_found'}, 404));
  await settle();
  assert.deepEqual(graded.state.records.map(row => row.id), ['b']);
  assert.equal(graded.state.refreshing.a, undefined);
  assert.equal(backend.waiting.length, 1, 'the cancelled refresh never ran');
});

test('delete removes the card, treats "already gone" as done and reports other failures', async () => {
  const {graded, backend} = await ready({}, {records: [card('a', 'psa', '1'), card('b', 'psa', '2'), card('c', 'psa', '3')]});
  assert.equal(await graded.remove('a'), '');
  backend.deleteReply = reply({detail: 'graded_card_not_found'}, 404);
  assert.equal(await graded.remove('b'), '');
  backend.deleteReply = reply({detail: 'boom'}, 500);
  assert.equal(await graded.remove('c'), 'graded_delete_failed');
  assert.deepEqual(graded.state.records.map(row => row.id), ['c']);
  assert.equal(graded.state.removing.size, 0);
  assert.equal(await graded.remove('missing'), 'graded_delete_failed');
  assert.deepEqual(backend.calls.filter(call => call.method === 'DELETE').map(call => call.path),
    ['/api/graded-cards/a', '/api/graded-cards/b', '/api/graded-cards/c']);
});

test('crop corners: a slightly inset default, clamped moves and a compact query value', () => {
  assert.deepEqual(fullCorners(1000, 500), [[40, 20], [960, 20], [960, 480], [40, 480]]);
  assert.deepEqual(fullCorners(10, 10, 0), [[0, 0], [10, 0], [10, 10], [0, 10]]);
  assert.deepEqual(clampCorner([-5, 900], 800, 600), [0, 600]);
  assert.equal(cornersParam([[1.234, 2], [3, 4.56], [5, 6], [7, 8]]), '1.2,2,3,4.6,5,6,7,8');
});

test('photo errors keep the server message and fall back by status', () => {
  assert.equal(imageError('graded_image_crop', 422), 'graded_image_crop');
  assert.equal(imageError('graded_card_not_found', 404), 'graded_error_missing');
  assert.equal(imageError('rate_limited', 429), 'graded_image_rate_limited');
  assert.equal(imageError(undefined, 500), 'graded_image_failed');
  assert.equal(imageError(undefined, 0), 'graded_error_network');
  for (const key of IMAGE_ERROR_KEYS) assert.ok(messages.en[key] && messages['zh-CN'][key], key);
});

test('photos are sent raw: detection, crop upload with corners, and restoring the official image', async () => {
  const saved = card('s', 'psa', '1', {images: [{url: 'https://official/front.jpg', side: 'front', uploaded: false}]});
  const calls = [];
  let fail = null;
  const api = async (url, options = {}) => {
    const {pathname, searchParams} = new URL(url, 'http://local'), method = options.method ?? 'GET';
    calls.push({method, pathname, corners: searchParams.get('corners'), type: options.headers?.['Content-Type'], body: options.body});
    if (fail) { const [status, detail] = fail; fail = null; return reply({detail}, status); }
    if (pathname === '/api/grading/providers') return reply({items: PROVIDERS});
    if (pathname === '/api/graded-cards' && method === 'GET') return reply({items: [saved]});
    if (pathname === '/api/graded-cards/groups') return reply({items: []});
    if (pathname === '/api/graded-cards/detect-card') return reply({width: 800, height: 600, corners: [[1, 2], [3, 4], [5, 6], [7, 8]]});
    if (method === 'PUT') return reply({...saved, images: [{url: 'https://cos/x.webp', side: 'front', uploaded: true}]});
    return reply(saved);
  };
  const graded = createGradedCards(() => {});
  await graded.activate(api);
  const photo = new Blob(['jpeg'], {type: 'image/jpeg'});
  assert.deepEqual(await graded.detectCard(photo), {width: 800, height: 600, corners: [[1, 2], [3, 4], [5, 6], [7, 8]]});
  assert.equal(calls.at(-1).type, 'image/jpeg');
  assert.equal(calls.at(-1).body, photo);
  const uploading = graded.uploadImage('s', 'front', photo, [[0, 0], [10, 0], [10, 20], [0, 20]]);
  assert.ok(graded.state.imageBusy.has('s:front'));
  assert.equal(await graded.uploadImage('s', 'front', photo, []), 'graded_image_failed', 'one change per side at a time');
  assert.equal(await uploading, '');
  assert.deepEqual(calls.at(-1), {method: 'PUT', pathname: '/api/graded-cards/s/images/front', corners: '0,0,10,0,10,20,0,20',
    type: 'image/jpeg', body: photo});
  assert.equal(graded.state.records[0].images[0].url, 'https://cos/x.webp');
  assert.equal(graded.state.imageBusy.size, 0);
  assert.equal(await graded.restoreImage('s', 'front'), '');
  assert.equal(calls.at(-1).method, 'DELETE');
  assert.equal(graded.state.records[0].images[0].url, 'https://official/front.jpg');
  fail = [422, 'graded_image_crop'];
  assert.equal(await graded.uploadImage('s', 'back', photo, [[0, 0], [1, 0], [1, 1], [0, 1]]), 'graded_image_crop');
  fail = [413, 'graded_image_too_large'];
  await assert.rejects(graded.detectCard(photo), error => error.key === 'graded_image_too_large');
  fail = [404, 'graded_card_not_found'];
  assert.equal(await graded.restoreImage('s', 'back'), 'graded_error_missing');
  assert.deepEqual(graded.state.records, [], 'a card deleted elsewhere disappears');
});

test('brightness calibration sends the photo raw and returns the calibrated image', async () => {
  const calibrated = new Blob(['calibrated'], {type: 'image/jpeg'}), calls = [];
  let status = 200;
  const api = async (url, options = {}) => {
    const {pathname} = new URL(url, 'http://local');
    calls.push({pathname, method: options.method, type: options.headers?.['Content-Type'], body: options.body});
    if (pathname === '/api/grading/providers') return reply({items: PROVIDERS});
    if (pathname === '/api/graded-cards' && !options.method) return reply({items: []});
    if (pathname === '/api/graded-cards/groups') return reply({items: []});
    if (pathname === '/api/graded-cards' && options.method === 'GET') return reply({items: []});
    return status === 200 ? {ok: true, status, blob: async () => calibrated} : reply(status === 422 ? {detail: 'graded_image_invalid'} : {}, status);
  };
  const graded = createGradedCards(() => {});
  await graded.activate(api);
  const photo = new Blob(['jpeg'], {type: 'image/jpeg'});
  assert.equal(await graded.calibrateImage(photo), calibrated);
  assert.deepEqual(calls.at(-1), {pathname: '/api/graded-cards/calibrate-image', method: 'POST', type: 'image/jpeg', body: photo});
  status = 422;
  await assert.rejects(graded.calibrateImage(photo), error => error.key === 'graded_image_invalid');
  status = 500;
  await assert.rejects(graded.calibrateImage(photo), error => error.key === 'graded_image_failed');
});

test('groups: create selects the tab, cards move and filter, deleting keeps cards ungrouped', async () => {
  const {graded, backend} = await ready({}, {records: [card('a', 'psa', '1'), card('b', 'cgc', '2')]});
  assert.equal(await graded.createGroup('Vault'), '');
  assert.deepEqual(graded.state.groups.map(group => group.name), ['Vault']);
  assert.equal(graded.state.group, 'g1');
  assert.deepEqual(graded.visible(), []);
  assert.equal(await graded.createGroup('Vault'), 'favorite_group_name_taken');
  assert.equal(await graded.move('a', 'g1'), '');
  assert.deepEqual(backend.calls.at(-1), {method: 'PUT', path: '/api/graded-cards/a/group', body: {group_id: 'g1'}});
  assert.deepEqual(graded.visible().map(row => row.id), ['a']);
  graded.select(null);
  assert.equal(graded.visible().length, 2);
  graded.select('missing');
  assert.equal(graded.state.group, null, 'unknown tabs are ignored');
  assert.equal(await graded.renameGroup('g1', 'Safe'), '');
  assert.equal(graded.state.groups[0].name, 'Safe');
  graded.select('g1');
  assert.equal(await graded.deleteGroup('g1'), '');
  assert.equal(graded.state.group, null);
  assert.deepEqual(graded.state.records.map(row => row.group_id), [null, null]);
  // A group deleted in another tab: the move fails and the page reloads.
  assert.equal(await graded.move('a', 'gone'), 'favorite_group_missing');
});

test('a new card is saved into the group chosen when it was added', async () => {
  const {graded, backend} = await ready({}, {groups: [{id: 'g1', name: 'Vault', created_at: 1}]});
  graded.add({provider: 'psa', cert_number: '7', group_id: 'g1'});
  assert.equal(backend.waiting[0].body.group_id, 'g1');
  backend.waiting[0].resolve(reply(card('n', 'psa', '7', {group_id: 'g1'}), 201));
  await settle();
  assert.equal(graded.state.records[0].group_id, 'g1');
  // The group went away before this lookup started: it is sent again, ungrouped.
  graded.add({provider: 'psa', cert_number: '8', group_id: 'g1'});
  backend.waiting[1].resolve(reply({detail: 'graded_group_not_found'}, 404));
  await settle();
  assert.equal(backend.waiting[2].body.group_id, null);
  assert.equal(graded.state.pending[0].status, 'loading');
});

test('display names are searched, sorted and saved without touching other fields', async () => {
  const rows = [card('a', 'psa', '1', {title: 'Zeta'}), card('b', 'psa', '2', {title: 'Beta', display_name: 'Alpha slab'})];
  assert.deepEqual(filterGraded(rows, {query: 'alpha'}).map(row => row.id), ['b']);
  assert.deepEqual(filterGraded(rows, {query: 'beta'}).map(row => row.id), ['b'], 'the certificate name still matches');
  assert.deepEqual(filterGraded(rows, {sort: 'title'}).map(row => row.id), ['b', 'a']);
  const {graded, backend} = await ready({}, {records: [card('s', 'psa', '1', {price: 5})]});
  graded.refresh('s');
  const saving = graded.setDisplayName('s', 'Mine');
  assert.ok(graded.state.nameBusy.has('s'));
  assert.deepEqual(backend.calls.at(-1), {method: 'PUT', path: '/api/graded-cards/s/display-name', body: {display_name: 'Mine'}});
  backend.waiting[1].resolve(reply({display_name: 'Mine'}));
  assert.equal(await saving, '');
  assert.equal(graded.state.records[0].display_name, 'Mine');
  assert.equal(graded.state.records[0].price, 5);
  // A refresh that answers later keeps the name saved meanwhile.
  backend.waiting[0].resolve(reply(card('s', 'psa', '1', {grade: '9'})));
  await settle();
  assert.equal(graded.state.records[0].display_name, 'Mine');
  const failing = graded.setDisplayName('s', '');
  backend.waiting[2].resolve(reply({detail: 'boom'}, 500));
  assert.equal(await failing, 'graded_display_name_failed');
  assert.equal(graded.state.records[0].display_name, 'Mine');
});
