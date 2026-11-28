"""Saved certificate API acceptance against the isolated PostgreSQL schema."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from auth_helpers import register_user
from tcg_toolkit.grading import lookup as grading
from tcg_toolkit.grading.concurrency import LookupSlots
from tcg_toolkit.persistence.models import GradedCard, User
from tcg_toolkit.persistence.runtime import open_database
from tcg_toolkit.web import app

ORIGIN = 'https://testserver'
PATH = '/api/graded-cards'


def certificate(number, provider='psa', grade='10'):
    return dict(provider=provider, cert_number=number, source_url=f'https://example.test/{number}',
                certificate=dict(title='Saved card', grade=grade, fields={'Number': number},
                                 population={'at_grade': 2, 'higher': None}),
                images=[dict(url='https://example.test/front.jpg', alt='Front', side='front', thumbnail_url=None)])


@pytest.fixture
def client(monkeypatch, postgres_schema):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    monkeypatch.setattr(grading, 'LOOKUP_SLOTS', LookupSlots())
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
    with open_database() as reopened, reopened.session() as db:
        assert {row.id for row in db.scalars(select(GradedCard))} == {row['id'] for row in saved}


def test_duplicate_is_detected_before_lookup(client, monkeypatch):
    saved = add(client).json()
    fetch_with(monkeypatch, lambda number: pytest.fail('duplicate must not fetch'))
    response = add(client, ' 00123 ')
    assert response.status_code == 409
    assert response.json()['detail'] == {'type': 'graded_card_duplicate', 'id': saved['id']}


def test_daily_quota_is_shared_by_paid_providers_and_lookup_routes(client, monkeypatch):
    monkeypatch.setattr(grading, 'DAILY_PAID_LOOKUP_LIMIT', 2)
    card = add(client).json()
    assert client.get('/api/grading/cgc/certificates/234').status_code == 200

    fetch_with(monkeypatch, lambda number: pytest.fail('quota must be checked before fetching'))
    denied = [client.get('/api/grading/psa/certificates/456'),
              add(client, '456'),
              client.post(f"{PATH}/{card['id']}/refresh", json={})]
    for response in denied:
        assert response.status_code == 429
        assert response.json()['detail'] == 'grading_daily_limit'
    for provider in ('ars', 'pcgcn'):
        assert client.get(f'/api/grading/{provider}/certificates/234').status_code == 200

    client.headers['Authorization'] = 'Bearer ' + register_user(client, 'bob').json()['token']
    assert client.get('/api/grading/cgc/certificates/234').status_code == 200


def test_failed_paid_lookup_counts_until_the_next_day(client, monkeypatch):
    monkeypatch.setattr(grading, 'DAILY_PAID_LOOKUP_LIMIT', 1)

    def unavailable(number):
        raise HTTPException(504, detail='psa_timeout')

    fetch_with(monkeypatch, unavailable)
    assert client.get('/api/grading/psa/certificates/123').status_code == 504
    path = '/api/grading/cgc/certificates/123'
    assert client.get(path).status_code == 429
    with app.state.database.session() as db:
        db.scalar(select(User).where(User.username == 'alice')).grading_lookup_date = date(2000, 1, 1)
    assert client.get(path).status_code == 200
    assert client.get(path).status_code == 429


def test_concurrent_paid_lookups_share_the_last_slot(client, monkeypatch):
    monkeypatch.setattr(grading, 'DAILY_PAID_LOOKUP_LIMIT', 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = pool.map(client.get, ['/api/grading/psa/certificates/123', '/api/grading/cgc/certificates/456'])
    assert sorted(response.status_code for response in responses) == [200, 429]


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
