"""Saved certificate API acceptance against the isolated PostgreSQL schema."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from auth_helpers import register_user
from tcg_toolkit import grading
from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.db_models import GradedCard
from tcg_toolkit.web import app

ORIGIN = 'https://testserver'
PATH = '/api/graded-cards'


def certificate(number, provider='psa', grade='10'):
    return dict(provider=provider, cert_number=number, source_url=f'https://example.test/{number}',
                certificate=dict(title='Saved card', grade=grade, fields={'Number': number},
                                 population={'at_grade': 2, 'higher': None}),
                images=[dict(url='https://example.test/front.jpg', alt='Front', side='front', thumbnail_url=None)])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    monkeypatch.setattr(grading, 'LOOKUP_SLOTS', grading.LookupSlots())
    for provider in grading.PROVIDERS.values():
        monkeypatch.setitem(grading.PROVIDERS, provider.id,
                            replace(provider, fetch=lambda number, id=provider.id: certificate(number, id)))
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as browser:
        response = register_user(browser)
        assert response.status_code == 201
        browser.headers['Authorization'] = 'Bearer ' + response.json()['token']
        yield browser


def fetch_with(monkeypatch, fetch):
    monkeypatch.setitem(grading.PROVIDERS, 'psa', replace(grading.PROVIDERS['psa'], fetch=fetch))


def add(client, number='00123', provider='psa'):
    return client.post(PATH, json={'provider': provider, 'cert_number': number})


def test_registry_and_saved_snapshots_persist_for_all_providers(client):
    registry = client.get('/api/grading/providers')
    assert registry.status_code == 200
    assert registry.headers['cache-control'] == 'no-store'
    assert [item['id'] for item in registry.json()['items']] == ['psa', 'cgc', 'ars', 'pcgcn']
    assert all(item['scan_patterns'] and item['timeout_seconds'] > 0 for item in registry.json()['items'])
    saved = []
    for provider in grading.PROVIDERS:
        response = add(client, ' 00123 ', provider)
        assert response.status_code == 201
        assert response.headers['cache-control'] == 'no-store'
        row = response.json()
        assert row['cert_number'] == '00123' and row['provider'] == provider
        # ARS folds its English-labelled fields away; every other provider shows them.
        assert row['grade'] == '10' and {**row['fields'], **row['english_fields']} == {'Number': '00123'}
        assert bool(row['english_fields']) == (provider == 'ars')
        assert row['population'] == {'at_grade': 2, 'higher': None}
        assert row['images'][0]['side'] == 'front'
        assert row['created_at'] == row['refreshed_at']
        assert (row['price'], row['currency']) == (None, 'JPY')
        saved.insert(0, row)
    response = client.get(PATH)
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['items'] == saved
    with UserStore() as reopened, reopened.session() as db:
        assert {row.id for row in db.scalars(select(GradedCard))} == {row['id'] for row in saved}


def test_duplicate_is_detected_before_lookup(client, monkeypatch):
    saved = add(client).json()
    fetch_with(monkeypatch, lambda number: pytest.fail('duplicate must not fetch'))
    response = add(client, ' 00123 ')
    assert response.status_code == 409
    assert response.json()['detail'] == {'type': 'graded_card_duplicate', 'id': saved['id']}


@pytest.mark.parametrize('body', [
    {'provider': 'unknown', 'cert_number': '1'}, {'provider': 'psa', 'cert_number': ''},
    {'provider': 'psa', 'cert_number': '１２３'}, {'provider': 'psa', 'cert_number': '1' * 33},
    {'provider': 'psa', 'cert_number': '1-2'}, {'provider': 'psa', 'cert_number': 123},
    {'provider': 'psa', 'cert_number': None}, {},
    {'provider': 'psa', 'cert_number': '123', 'price': '-1'},
    {'provider': 'psa', 'cert_number': '123', 'price': '1.001'},
    {'provider': 'psa', 'cert_number': '123', 'currency': 'USD'},
])
def test_invalid_input_does_not_lookup_or_write(client, monkeypatch, body):
    fetch_with(monkeypatch, lambda number: pytest.fail('invalid input must not fetch'))
    assert client.post(PATH, json=body).status_code == 422
    assert client.get(PATH).json() == {'items': []}


def test_authentication_write_guards_and_account_isolation(client, monkeypatch):
    saved = add(client).json()
    operations = [('POST', PATH), ('POST', f"{PATH}/{saved['id']}/refresh"),
                  ('PUT', f"{PATH}/{saved['id']}/price"), ('DELETE', f"{PATH}/{saved['id']}")]
    for method, path in operations:
        assert client.request(method, path, json={}, headers={'Origin': 'https://evil.test'}).status_code == 403
        assert client.request(method, path, content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    bob = register_user(client, 'bob').json()
    client.headers['Authorization'] = 'Bearer ' + bob['token']
    assert client.get(PATH).json() == {'items': []}
    for method, path in operations[1:]:
        response = client.request(method, path, json={})
        assert response.status_code == 404
        assert response.json()['detail'] == 'graded_card_not_found'
    assert add(client).status_code == 201  # same certificate may belong to another account
    client.headers.pop('Authorization')
    fetch_with(monkeypatch, lambda number: pytest.fail('unauthenticated lookup'))
    for method, path in [('GET', PATH), ('GET', '/api/grading/providers'), *operations]:
        response = client.request(method, path, json={})
        assert response.status_code == 401
        assert response.headers['cache-control'] == 'no-store'


def test_refresh_replaces_snapshot_and_failure_keeps_previous_data(client, monkeypatch):
    response = client.post(PATH, json={'provider': 'psa', 'cert_number': '00123', 'price': '12.34', 'currency': 'cny'})
    assert response.status_code == 201
    saved = response.json()
    assert (saved['price'], saved['currency']) == (12.34, 'CNY')
    price_path = f"{PATH}/{saved['id']}/price"
    assert client.put(price_path, json={'price': '1000000000000', 'currency': 'CAD'}).status_code == 422
    price = client.put(price_path, json={'price': '0', 'currency': 'CAD'})
    assert price.json() == {'price': 0, 'currency': 'CAD'}
    path = f"{PATH}/{saved['id']}/refresh"
    fetch_with(monkeypatch, lambda number: certificate(number, grade='9'))
    response = client.post(path, json={})
    assert response.status_code == 200
    updated = response.json()
    assert updated['id'] == saved['id'] and updated['created_at'] == saved['created_at']
    assert updated['grade'] == '9'
    assert (updated['price'], updated['currency']) == (0, 'CAD')
    assert updated['refreshed_at'] >= saved['refreshed_at']

    def fail(number):
        raise HTTPException(504, detail='psa_timeout')

    fetch_with(monkeypatch, fail)
    assert client.post(path, json={}).status_code == 504
    assert add(client, '456').status_code == 504
    assert client.get(PATH).json()['items'] == [updated]
    assert client.put(price_path, json={'price': None, 'currency': 'JPY'}).json() == {'price': None, 'currency': 'JPY'}
    assert client.get(PATH).json()['items'] == [{**updated, 'price': None, 'currency': 'JPY'}]
    response = client.request('DELETE', f"{PATH}/{saved['id']}", json={})
    assert response.status_code == 204 and not response.content
    assert response.headers['cache-control'] == 'no-store'
    assert client.get(PATH).json() == {'items': []}


def test_concurrent_add_returns_one_saved_row_and_one_conflict(client, monkeypatch):
    barrier = Barrier(2)

    def fetch(number):
        barrier.wait(timeout=10)
        return certificate(number)

    fetch_with(monkeypatch, fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: add(client), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    created = next(response.json() for response in responses if response.status_code == 201)
    conflict = next(response.json() for response in responses if response.status_code == 409)
    assert conflict['detail']['id'] == created['id']
    assert client.get(PATH).json()['items'] == [created]


def test_refresh_does_not_hold_a_connection_or_resurrect_deleted_card(client, monkeypatch):
    saved = add(client).json()
    entered, release = Event(), Event()

    def fetch(number):
        entered.set()
        assert release.wait(timeout=10)
        return certificate(number)

    fetch_with(monkeypatch, fetch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, f"{PATH}/{saved['id']}/refresh", json={})
        try:
            assert entered.wait(timeout=10)
            assert app.state.auth.store.engine.pool.checkedout() == 0
            assert client.request('DELETE', f"{PATH}/{saved['id']}", json={}).status_code == 204
        finally:
            release.set()
        assert future.result(timeout=10).status_code == 404
    assert client.get(PATH).json() == {'items': []}


def test_lookup_limits_are_shared_and_released_after_failure(client, monkeypatch):
    slots = grading.LookupSlots(per_user=1, total=1)
    monkeypatch.setattr(grading, 'LOOKUP_SLOTS', slots)
    with slots.hold('other-user'):
        for response in (add(client), client.get('/api/grading/psa/certificates/00123')):
            assert response.status_code == 429 and response.json()['detail'] == 'grading_busy'
        assert client.get(PATH).status_code == 200
    with pytest.raises(RuntimeError), slots.hold('alice'):
        raise RuntimeError('provider failed')
    assert add(client).status_code == 201
    per_user = grading.LookupSlots(per_user=1, total=2)
    with per_user.hold('alice'):
        with pytest.raises(HTTPException) as error, per_user.hold('alice'):
            pytest.fail('same user exceeded cap')
        assert error.value.status_code == 429
        with per_user.hold('bob'):
            pass


def test_saved_cards_show_original_names_and_fold_ars_english_fields(client, monkeypatch):
    ars_fields = {'カード名称': '学生兼研究者 式部 茉優', 'ブランド': 'Lycee Overture',
                  'Card Name': 'Student and Researcher, Mayu Shikibe', 'Brand': 'Lycee Overture'}
    results = {
        'ars': dict(certificate('047088950114', 'ars'), certificate=dict(
            title='Student and Researcher, Mayu Shikibe', grade='10', fields=ars_fields, population={})),
        'pcgcn': dict(certificate('10198854', 'pcgcn'), certificate=dict(
            title='宫小路瑞穗', grade='10', fields={'Card Name': '宫小路瑞穗', 'Original Name': '宮小路瑞穂'}, population={})),
    }
    for provider, result in results.items():
        monkeypatch.setitem(grading.PROVIDERS, provider,
                            replace(grading.PROVIDERS[provider], fetch=lambda number, result=result: result))
    ars = add(client, '047088950114', 'ars').json()
    assert ars['title'] == '学生兼研究者 式部 茉優'
    assert ars['fields'] == {'カード名称': '学生兼研究者 式部 茉優', 'ブランド': 'Lycee Overture'}
    assert ars['english_fields'] == {'Card Name': 'Student and Researcher, Mayu Shikibe', 'Brand': 'Lycee Overture'}
    pcg = add(client, '10198854', 'pcgcn').json()
    assert pcg['title'] == '宮小路瑞穂' and pcg['english_fields'] == {}
    # Applied when reading, so older snapshots show the same way; a PCG-CN card without an
    # original name keeps the Chinese name, and an unknown provider shows what was saved.
    assert grading.present('pcgcn', {'title': '宫小路瑞穗', 'fields': {'Card Name': '宫小路瑞穗'}})[0] == '宫小路瑞穗'
    assert grading.present('removed-provider', {'title': 'Kept', 'fields': {'A': '1'}}) == ('Kept', {'A': '1'}, {})


def slow_fetch(started, closed):
    """Stands in for an upstream request: blocks until its client is closed."""
    def fetch(number):
        class Client:
            def close(self):
                closed.set()
        grading._cancellable(Client())
        started.set()
        assert closed.wait(timeout=10)
        raise HTTPException(502, detail='psa_unavailable')
    return fetch


@pytest.mark.parametrize('refreshing', [False, True])
def test_cancel_stops_the_upstream_request_and_nothing_is_saved(client, monkeypatch, refreshing):
    saved = add(client).json() if refreshing else None
    started, closed = Event(), Event()
    fetch_with(monkeypatch, slow_fetch(started, closed))
    request_id = 'lookup-0001'
    path, body = ((f"{PATH}/{saved['id']}/refresh", {'request_id': request_id}) if refreshing
                  else (PATH, {'provider': 'psa', 'cert_number': '777', 'request_id': request_id}))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, path, json=body)
        assert started.wait(timeout=10)
        response = client.request('DELETE', f'{PATH}/lookups/{request_id}', json={})
        assert response.status_code == 204 and response.headers['cache-control'] == 'no-store'
        assert closed.is_set()
        result = future.result(timeout=10)
    assert result.status_code == 409 and result.json()['detail'] == 'grading_lookup_cancelled'
    assert client.get(PATH).json()['items'] == ([saved] if refreshing else [])
    fetch_with(monkeypatch, lambda number: certificate(number))
    assert add(client, '778').status_code == 201, 'the cancelled lookup released its slot'


def test_cancel_before_start_after_save_and_from_another_account(client, monkeypatch):
    fetch_with(monkeypatch, lambda number: pytest.fail('a lookup cancelled before it started must not fetch'))
    assert client.request('DELETE', f'{PATH}/lookups/early-0001', json={}).status_code == 204
    response = client.post(PATH, json={'provider': 'psa', 'cert_number': '1', 'request_id': 'early-0001'})
    assert response.status_code == 409 and response.json()['detail'] == 'grading_lookup_cancelled'
    fetch_with(monkeypatch, lambda number: certificate(number))
    assert client.post(PATH, json={'provider': 'psa', 'cert_number': '2', 'request_id': 'late-00001'}).status_code == 201
    response = client.request('DELETE', f'{PATH}/lookups/late-00001', json={})
    assert response.status_code == 409 and response.json()['detail'] == 'graded_lookup_finished'
    # Request ids belong to an account: bob cannot stop alice's lookup.
    started, closed = Event(), Event()
    fetch_with(monkeypatch, slow_fetch(started, closed))
    alice = client.headers['Authorization']
    bob = 'Bearer ' + register_user(client, 'bob').json()['token']
    client.headers['Authorization'] = alice
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, PATH, json={'provider': 'psa', 'cert_number': '3', 'request_id': 'shared-001'},
                             headers={'Authorization': alice})
        assert started.wait(timeout=10)
        client.request('DELETE', f'{PATH}/lookups/shared-001', json={}, headers={'Authorization': bob})
        assert not closed.wait(timeout=0.3)
        client.request('DELETE', f'{PATH}/lookups/shared-001', json={}, headers={'Authorization': alice})
        assert future.result(timeout=10).status_code == 409
    for bad in ('short', 'x' * 65, 'has_underscore'):
        assert client.request('DELETE', f'{PATH}/lookups/{bad}', json={}).status_code == 422
        assert client.post(PATH, json={'provider': 'psa', 'cert_number': '4', 'request_id': bad}).status_code == 422


def test_registry_forgets_outcomes_after_a_while():
    now = [0.0]
    registry = grading.LookupRegistry(clock=lambda: now[0])
    assert registry.cancel('alice', 'early-0001') == 'cancelled'
    with registry.track('alice', 'early-0001') as token:
        assert token.cancelled
    with registry.track('alice', 'saved-0001') as token:
        assert token.finish()
    assert registry.cancel('alice', 'saved-0001') == 'finished'
    now[0] = registry.TTL + 1
    with registry.track('alice', 'early-0001') as token:
        assert not token.cancelled, 'an old cancel does not stop a new lookup'
    assert registry._outcomes.keys() == {('alice', 'early-0001')}


def test_cancelling_closes_a_real_http_client_mid_request(monkeypatch):
    """The production fetchers register their httpx client; closing it ends a slow read."""
    import http.server
    import threading
    import time

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(5)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(grading, 'ARS_URL', f'http://127.0.0.1:{server.server_port}/')
    monkeypatch.setattr(grading, 'LOOKUP_SLOTS', grading.LookupSlots())
    registry = grading.LookupRegistry()

    def lookup():
        with registry.track('alice', 'real-00001'):
            return grading.lookup_certificate('ars', '047088950114', 'alice')

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(lookup)
            time.sleep(0.5)
            started = time.monotonic()
            assert registry.cancel('alice', 'real-00001') == 'cancelled'
            with pytest.raises(HTTPException) as error:
                future.result(timeout=10)
            assert error.value.detail == 'grading_lookup_cancelled'
            assert time.monotonic() - started < 2
    finally:
        server.shutdown()


def test_groups_create_rename_move_and_delete_keep_cards(client, monkeypatch):
    groups = f'{PATH}/groups'
    assert client.get(groups).json() == {'items': []}
    slabs = client.post(groups, json={'name': '  Slabs  '})
    assert slabs.status_code == 201 and slabs.json()['name'] == 'Slabs'
    slabs = slabs.json()
    assert client.post(groups, json={'name': 'Slabs'}).json()['detail'] == 'graded_group_name_taken'
    assert client.post(groups, json={'name': ' '}).status_code == 422
    vault = client.post(groups, json={'name': 'Vault'}).json()
    assert client.put(f"{groups}/{vault['id']}", json={'name': 'Slabs'}).status_code == 409
    assert client.put(f"{groups}/{vault['id']}", json={'name': 'Safe'}).json()['name'] == 'Safe'
    assert [row['name'] for row in client.get(groups).json()['items']] == ['Slabs', 'Safe']

    # New cards may go straight into a group; an unknown group is rejected before the lookup.
    saved = client.post(PATH, json={'provider': 'psa', 'cert_number': '1', 'group_id': slabs['id']})
    assert saved.status_code == 201 and saved.json()['group_id'] == slabs['id']
    fetch_with(monkeypatch, lambda number: pytest.fail('lookup for a missing group'))
    missing = client.post(PATH, json={'provider': 'psa', 'cert_number': '2', 'group_id': 'nope'})
    assert (missing.status_code, missing.json()['detail']) == (404, 'graded_group_not_found')
    plain = client.post(PATH, json={'provider': 'cgc', 'cert_number': '3'}).json()
    assert plain['group_id'] is None

    card = saved.json()['id']
    moved = client.put(f'{PATH}/{plain["id"]}/group', json={'group_id': vault['id']})
    assert moved.status_code == 200 and moved.json()['group_id'] == vault['id']
    assert moved.json()['title'] == 'Saved card'  # the full record comes back
    assert client.put(f'{PATH}/{card}/group', json={'group_id': 'nope'}).status_code == 404
    assert client.put(f'{PATH}/missing/group', json={'group_id': None}).json()['detail'] == 'graded_card_not_found'
    assert client.put(f'{PATH}/{card}/group', json={'group_id': None}).json()['group_id'] is None
    assert client.put(f'{PATH}/{card}/group', json={'group_id': slabs['id']}).json()['group_id'] == slabs['id']

    # Deleting a group keeps its cards, ungrouped.
    assert client.request('DELETE', f"{groups}/{slabs['id']}", json={}).status_code == 204
    assert client.request('DELETE', f"{groups}/{slabs['id']}", json={}).status_code == 404
    by_id = {row['id']: row['group_id'] for row in client.get(PATH).json()['items']}
    assert by_id == {card: None, plain['id']: vault['id']}

    # Groups are per account.
    bob = register_user(client, 'bob').json()
    client.headers['Authorization'] = 'Bearer ' + bob['token']
    assert client.get(groups).json() == {'items': []}
    assert client.put(f"{groups}/{vault['id']}", json={'name': 'Mine'}).status_code == 404
    assert client.post(groups, json={'name': 'Safe'}).status_code == 201
    for method, path in [('POST', groups), ('PUT', f"{groups}/{vault['id']}"), ('DELETE', f"{groups}/{vault['id']}"),
                         ('PUT', f'{PATH}/{card}/group')]:
        assert client.request(method, path, json={'name': 'x'}, headers={'Origin': 'https://evil.test'}).status_code == 403


def test_group_deleted_during_lookup_saves_the_card_ungrouped(client, monkeypatch):
    group = client.post(f'{PATH}/groups', json={'name': 'Temp'}).json()

    def fetch(number):
        assert client.request('DELETE', f"{PATH}/groups/{group['id']}", json={}).status_code == 204
        return certificate(number)

    fetch_with(monkeypatch, fetch)
    saved = client.post(PATH, json={'provider': 'psa', 'cert_number': '9', 'group_id': group['id']})
    assert saved.status_code == 201 and saved.json()['group_id'] is None


def test_display_name_is_optional_trimmed_and_kept_across_refresh(client):
    saved = add(client).json()
    assert saved['display_name'] is None
    path = f"{PATH}/{saved['id']}/display-name"
    assert client.put(path, json={'display_name': '  My Mayu  '}).json() == {'display_name': 'My Mayu'}
    assert client.get(PATH).json()['items'][0]['display_name'] == 'My Mayu'
    refreshed = client.post(f"{PATH}/{saved['id']}/refresh", json={}).json()
    assert (refreshed['display_name'], refreshed['title']) == ('My Mayu', 'Saved card')
    assert client.put(path, json={'display_name': 'x' * 101}).status_code == 422
    assert client.put(path, json={'display_name': 5}).status_code == 422
    assert client.put(path, json={'display_name': '   '}).json() == {'display_name': None}
    assert client.put(path, json={'display_name': 'Again'}).json() == {'display_name': 'Again'}
    assert client.put(path, json={}).json() == {'display_name': None}
    assert client.put(path, json={'display_name': 'x'}, headers={'Origin': 'https://evil.test'}).status_code == 403
    missing = client.put(f'{PATH}/missing/display-name', json={'display_name': 'x'})
    assert (missing.status_code, missing.json()['detail']) == (404, 'graded_card_not_found')
    bob = register_user(client, 'bob').json()
    client.headers['Authorization'] = 'Bearer ' + bob['token']
    assert client.put(path, json={'display_name': 'mine'}).status_code == 404
