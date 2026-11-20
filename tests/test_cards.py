from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from auth_helpers import register_user
from tcg_toolkit.collection.transfer import XLSX_MEDIA_TYPE
from tcg_toolkit.web import app

ORIGIN = 'https://testserver'
CARD = dict(game='ws', card_number='ABC-001', rarity='SR', name='Card', quantity=2, notes='Mine')


def login(client, name):
    response = register_user(client, name)
    assert response.status_code == 201, response.text
    client.headers['Authorization'] = 'Bearer ' + response.json()['token']


@pytest.fixture
def client(monkeypatch, postgres_schema):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        login(client, 'alice')
        yield client


@pytest.mark.parametrize('format,media_type', [('json', 'application/json'), ('xlsx', XLSX_MEDIA_TYPE)])
def test_import_exported_cards_into_current_account(client, format, media_type):
    group = client.post('/api/cards/groups', json={'name': 'Binder'}).json()
    card = {**CARD, 'card_number': '00123', 'price': 12.34, 'currency': 'CAD',
        'quantity': 9007199254740991, 'notes': '=1+1\n备注',
        'image_url': 'https://example.com/card.png'}
    saved = client.post('/api/cards', json={**card, 'group_id': group['id']}).json()
    exported = client.get('/api/cards/export', params={'format': format}).content
    login(client, 'bob')
    path = f'/api/cards/import?format={format}'
    headers = {'Content-Type': media_type}
    response = client.post(path, content=exported, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {'imported': 1, 'skipped': 0}
    [imported] = client.get('/api/cards').json()['items']
    assert imported['id'] != saved['id']
    assert {key: imported[key] for key in card} == {**card, 'game': 'WS'}
    [restored_group] = client.get('/api/cards/groups').json()['items']
    assert restored_group['name'] == 'Binder'
    assert imported['group_id'] == restored_group['id'] != group['id']
    client.put('/api/cards/' + imported['id'], json={**imported, 'quantity': 0})
    assert client.post(path, content=exported, headers=headers).json() == {'imported': 0, 'skipped': 1}
    assert client.get('/api/cards').json()['items'][0]['quantity'] == 0


def test_import_rejects_invalid_rows_without_writing_any_cards(client):
    path = '/api/cards/import?format=json'
    response = client.post(path, json={'items': [CARD, {**CARD, 'name': ''}]})
    assert response.status_code == 422
    assert response.json()['detail'] == {'code': 'cards_import_row', 'row': 2}
    assert client.get('/api/cards').json() == {'items': []}
    assert client.post(path, json={'items': [CARD]}, headers={'Origin': 'https://evil.test'}).status_code == 403
    client.headers.pop('Authorization')
    assert client.post(path, json={'items': [CARD]}).status_code == 401


def test_crud_duplicates_persistence(client):
    response = client.post('/api/cards', json={**CARD, 'game': ' Ws ', 'name': ' Card '})
    assert response.status_code == 201
    row = response.json()
    assert row['game'] == 'WS' and row['name'] == 'Card'
    assert (row['price'], row['currency']) == (None, 'JPY')
    duplicate = client.post('/api/cards', json={**CARD, 'rarity': ' sr ', 'card_number': ' abc-001 '})
    assert duplicate.status_code == 409 and duplicate.json()['detail']['id'] == row['id']
    other = client.post('/api/cards', json={**CARD, 'game': 'Custom game'}).json()
    assert client.put('/api/cards/' + other['id'], json=CARD).status_code == 409
    edited = client.put('/api/cards/' + row['id'], json={**CARD, 'quantity': 0, 'notes': ''}).json()
    assert edited['quantity'] == 0 and edited['notes'] == ''
    assert [r['id'] for r in client.get('/api/cards').json()['items']] == [other['id'], row['id']]
    assert client.request('DELETE', '/api/cards/' + row['id'], json={}).status_code == 204
    assert client.request('DELETE', '/api/cards/' + row['id'], json={}).status_code == 404


def test_cards_and_groups_belong_to_their_account(client):
    alice_token = client.headers['Authorization']
    group = client.post('/api/cards/groups', json={'name': 'Deck'}).json()
    saved = client.post('/api/cards', json={**CARD, 'group_id': group['id']}).json()
    login(client, 'bob')
    assert client.get('/api/cards').json() == {'items': []}
    assert client.get('/api/cards/export', params={'format': 'json'}).json() == {'items': []}
    assert client.request('DELETE', '/api/cards/' + saved['id'], json={}).status_code == 404
    assert client.post('/api/cards', json={**CARD, 'group_id': group['id']}).status_code == 404
    assert client.post('/api/cards', json=CARD).status_code == 201
    client.headers['Authorization'] = alice_token
    assert client.get('/api/cards', params={'group_id': group['id']}).json()['items'] == [saved]
    assert client.request('DELETE', '/api/cards/groups/' + group['id'], json={}).status_code == 204
    assert client.get('/api/cards').json()['items'] == [{**saved, 'group_id': None}]


def test_purchase_import_merges_card_identities_and_replays_concurrent_confirmations(client):
    old_group = client.post('/api/cards/groups', json={'name': 'Binder'}).json()['id']
    new_group = client.post('/api/cards/groups', json={'name': 'New purchases'}).json()['id']
    saved = client.post('/api/cards', json={**CARD, 'group_id': old_group, 'price': '12.50', 'currency': 'CAD'}).json()
    ws = {**CARD, 'card_number': 'abc-001', 'name': 'Official name', 'notes': '', 'quantity': 3}
    wsr = {**CARD, 'game': 'WSR', 'card_number': 'OS01/R01-047S', 'name': 'New', 'quantity': 6}
    body = {'items': [ws, wsr, {**wsr, 'rarity': 'sr', 'quantity': 1}], 'group_id': new_group}
    path, headers = '/api/cards/purchases', {'Idempotency-Key': 'confirmation-1'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(path, json=body, headers=headers), range(2)))
    for response in responses:
        assert response.status_code == 200, response.text
        assert response.json() == {'created': 1, 'updated': 1, 'quantity': 10}
    assert client.post(path, json={'items': [ws]}, headers=headers).status_code == 409
    added, updated = client.get('/api/cards').json()['items']
    assert (added['quantity'], added['group_id'], added['game']) == (7, new_group, 'WSR')
    assert updated == {**saved, 'quantity': 5, 'updated_at': updated['updated_at']}

    login(client, 'bob')
    assert client.post(path, json=body, headers=headers).status_code == 404
    assert client.post(path, json={'items': [ws]}, headers=headers).json() == {'created': 1, 'updated': 0, 'quantity': 3}
    assert len(client.get('/api/cards').json()['items']) == 1


def test_purchase_import_rolls_back_overflow_and_can_retry_failed_confirmation(client):
    path, headers = '/api/cards/purchases', {'Idempotency-Key': 'confirmation-2'}
    saved = client.post('/api/cards', json={**CARD, 'quantity': 9007199254740991}).json()
    other = {**CARD, 'card_number': 'ABC-002', 'quantity': 1}
    response = client.post(path, json={'items': [other, CARD]}, headers=headers)
    assert response.status_code == 422 and response.json()['detail'] == 'card_quantity_overflow'
    assert client.get('/api/cards').json()['items'] == [saved]
    for invalid in [0, -1, 1.5]:
        assert client.post(path, json={'items': [{**other, 'quantity': invalid}]}, headers=headers).status_code == 422
    assert client.post(path, json={'items': [other]}).status_code == 422
    assert client.post(path, json={'items': [other]}, headers=headers).json() == {'created': 1, 'updated': 0, 'quantity': 1}
