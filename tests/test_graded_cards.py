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
        assert row['grade'] == '10' and row['fields'] == {'Number': '00123'}
        assert row['population'] == {'at_grade': 2, 'higher': None}
        assert row['images'][0]['side'] == 'front'
        assert row['created_at'] == row['refreshed_at']
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
])
def test_invalid_input_does_not_lookup_or_write(client, monkeypatch, body):
    fetch_with(monkeypatch, lambda number: pytest.fail('invalid input must not fetch'))
    assert client.post(PATH, json=body).status_code == 422
    assert client.get(PATH).json() == {'items': []}


def test_authentication_write_guards_and_account_isolation(client, monkeypatch):
    saved = add(client).json()
    operations = [('POST', PATH), ('POST', f"{PATH}/{saved['id']}/refresh"), ('DELETE', f"{PATH}/{saved['id']}")]
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
    saved = add(client).json()
    path = f"{PATH}/{saved['id']}/refresh"
    fetch_with(monkeypatch, lambda number: certificate(number, grade='9'))
    response = client.post(path, json={})
    assert response.status_code == 200
    updated = response.json()
    assert updated['id'] == saved['id'] and updated['created_at'] == saved['created_at']
    assert updated['grade'] == '9'
    assert updated['refreshed_at'] >= saved['refreshed_at']

    def fail(number):
        raise HTTPException(504, detail='psa_timeout')

    fetch_with(monkeypatch, fail)
    assert client.post(path, json={}).status_code == 504
    assert add(client, '456').status_code == 504
    assert client.get(PATH).json()['items'] == [updated]
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
