from auth_helpers import register_user

import pytest
from fastapi.testclient import TestClient

from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.web import app

ORIGIN = 'https://testserver'
CARD = dict(game='ws', card_number='ABC-001', rarity='SR', name='Card', quantity=2, notes='Mine')


def login(client, name):
    data = register_user(client, name).json()
    client.headers['Authorization'] = 'Bearer ' + data['token']
    return data['token']


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        login(client, 'alice')
        yield client


def test_crud_duplicates_persistence(client):
    response = client.post('/api/cards', json={**CARD, 'game': ' Ws ', 'name': ' Card '})
    assert response.status_code == 201
    row = response.json()
    assert row['game'] == 'WS' and row['name'] == 'Card'
    duplicate = client.post('/api/cards', json={**CARD, 'rarity': ' sr ', 'card_number': ' abc-001 '})
    assert duplicate.status_code == 409 and duplicate.json()['detail']['id'] == row['id']
    other = client.post('/api/cards', json={**CARD, 'game': 'Custom game'}).json()
    assert client.put('/api/cards/' + other['id'], json=CARD).status_code == 409
    edited = client.put('/api/cards/' + row['id'], json={**CARD, 'quantity': 0, 'notes': ''}).json()
    assert edited['quantity'] == 0 and edited['notes'] == ''
    assert [r['id'] for r in client.get('/api/cards').json()['items']] == [other['id'], row['id']]
    with UserStore(app.state.auth.store.conninfo).connect() as db:
        assert db.execute('SELECT quantity FROM owned_cards WHERE id=%s', (row['id'],)).fetchone()['quantity'] == 0
    assert client.request('DELETE', '/api/cards/' + row['id'], json={}).status_code == 204
    assert client.request('DELETE', '/api/cards/' + row['id'], json={}).status_code == 404


def test_user_isolation_and_guards(client):
    alice_token = client.headers['Authorization']
    saved = client.post('/api/cards', json=CARD).json()
    login(client, 'bob')
    assert client.get('/api/cards').json() == {'items': []}
    for method in ['PUT', 'DELETE']:
        assert client.request(method, '/api/cards/' + saved['id'], json=CARD).status_code == 404
    assert client.post('/api/cards', json=CARD).status_code == 201
    client.headers['Authorization'] = alice_token
    assert client.get('/api/cards').json()['items'] == [saved]
    for method, path in [('POST', ''), ('PUT', '/' + saved['id']), ('DELETE', '/' + saved['id'])]:
        assert client.request(method, '/api/cards' + path, json=CARD, headers={'Origin': 'https://evil.test'}).status_code == 403
        assert client.request(method, '/api/cards' + path, content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    client.headers.pop('Authorization')
    for method, path in [('GET', ''), ('POST', ''), ('PUT', '/x'), ('DELETE', '/x')]:
        response = client.request(method, '/api/cards' + path, json=CARD)
        assert response.status_code == 401 and response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('field,value', [('game', ' '), ('name', ''), ('rarity', '\t'), ('card_number', ' '),
    ('quantity', -1), ('quantity', 1.5), ('quantity', True), ('quantity', '2'), ('quantity', 9007199254740992)])
def test_validation(client, field, value):
    assert client.post('/api/cards', json={**CARD, field: value}).status_code == 422


def test_sev_brands_are_independent_and_aliases_share_identity(client):
    # Same number and shared rarity can coexist across brands.
    for game, expected in [('sev', 'SEV'), ('sev_en', 'SEV EN')]:
        response = client.post('/api/cards', json={**CARD, 'game': game, 'rarity': 'SP'})
        assert response.status_code == 201
        assert response.json()['game'] == expected
    assert client.post('/api/cards', json={**CARD, 'game': ' SEV EN ', 'rarity': 'SP'}).status_code == 409
    rows = client.get('/api/cards').json()['items']
    assert {row['game'] for row in rows} == {'SEV', 'SEV EN'}


def test_unicode_keys_defaults_and_cors(client):
    body = {**CARD, 'game': 'Straße', 'rarity': 'Rare'}
    body.pop('quantity'); body.pop('notes')
    saved = client.post('/api/cards', json=body).json()
    assert saved['quantity'] == 1 and saved['notes'] == ''
    assert client.post('/api/cards', json={**body, 'game': 'STRASSE'}).status_code == 409
    response = client.options('/api/cards/x', headers={'Access-Control-Request-Method': 'PUT', 'Access-Control-Request-Headers': 'authorization,content-type'})
    assert response.status_code == 200
    assert 'PUT' in response.headers['access-control-allow-methods']




def test_image_persistence_update_and_clear(client):
    image = 'https://example.com/card.png'
    saved = client.post('/api/cards', json={**CARD, 'image_url': image}).json()
    assert saved['image_url'] == image
    assert client.get('/api/cards').json()['items'][0]['image_url'] == image
    with UserStore(app.state.auth.store.conninfo).connect() as db:
        assert db.execute('SELECT image_url FROM owned_cards WHERE id=%s', (saved['id'],)).fetchone()['image_url'] == image
    response = client.put('/api/cards/' + saved['id'], json={**CARD, 'image_url': None})
    assert response.status_code == 200 and response.json()['image_url'] == ''
    for invalid in ['javascript:alert(1)', 'data:image/png;base64,test', '/relative.png']:
        assert client.put('/api/cards/' + saved['id'], json={**CARD, 'image_url': invalid}).status_code == 422




def test_ws_en_is_separate_inventory_brand(client):
    for game, expected in [('WS', 'WS'), ('ws_en', 'WS EN')]:
        response = client.post('/api/cards', json={**CARD, 'game': game})
        assert response.status_code == 201
        assert response.json()['game'] == expected
    assert client.post('/api/cards', json={**CARD, 'game': 'WS EN'}).status_code == 409
