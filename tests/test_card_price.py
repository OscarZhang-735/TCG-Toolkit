from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy.exc import IntegrityError

from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.cards import XLSX_MEDIA_TYPE
from tcg_toolkit.db_models import OwnedCard
from tcg_toolkit.web import app
from test_cards import CARD, client, login


@pytest.mark.parametrize('price', [None, 0, 12.34, '0.10', '999999999999.99'])
def test_price_persistence_and_listing(client, price):
    response = client.post('/api/cards', json={**CARD, 'price': price})
    assert response.status_code == 201
    saved = response.json()
    assert saved['price'] == (float(price) if price is not None else None)
    assert client.get('/api/cards').json()['items'] == [saved]
    with UserStore(app.state.auth.store.conninfo) as store, store.session() as db:
        assert db.get(OwnedCard, saved['id']).price == (Decimal(str(price)) if price is not None else None)


def test_price_omission_update_clear_and_isolation(client):
    saved = client.post('/api/cards', json=CARD).json()
    assert saved['price'] is None
    path = f"/api/cards/{saved['id']}"
    assert client.put(path, json={**CARD, 'price': 19.95}).json()['price'] == 19.95
    assert client.put(path, json={**CARD, 'quantity': 3}).json()['price'] == 19.95
    assert client.put(path, json={**CARD, 'price': 0}).json()['price'] == 0
    assert client.put(path, json={**CARD, 'price': None}).json()['price'] is None
    token = client.headers['Authorization']
    login(client, 'bob')
    assert client.get('/api/cards').json() == {'items': []}
    assert client.put(path, json={**CARD, 'price': 1}).status_code == 404
    client.headers['Authorization'] = token
    assert client.get('/api/cards').json()['items'][0]['price'] is None


@pytest.mark.parametrize('price', [-1, -0.01, 1.001, 1000000000000, True, '', 'abc', 'NaN', 'Infinity', '-Infinity'])
def test_invalid_price_rejected_on_add_and_update(client, price):
    assert client.post('/api/cards', json={**CARD, 'price': price}).status_code == 422
    saved = client.post('/api/cards', json={**CARD, 'price': 10.01}).json()
    assert client.put(f"/api/cards/{saved['id']}", json={**CARD, 'price': price}).status_code == 422
    assert client.get('/api/cards').json()['items'] == [saved]


def test_price_survives_group_moves_and_duplicate_conflicts(client):
    group = client.post('/api/cards/groups', json={'name': 'Deck'}).json()
    saved = client.post('/api/cards', json={**CARD, 'price': 12.34, 'group_id': group['id']}).json()
    assert client.post('/api/cards', json={**CARD, 'price': 99}).status_code == 409
    assert client.put(f"/api/cards/{saved['id']}/group", json={'group_id': None}).json()['price'] == 12.34
    client.put(f"/api/cards/{saved['id']}/group", json={'group_id': group['id']})
    assert client.request('DELETE', f"/api/cards/groups/{group['id']}", json={}).status_code == 204
    assert client.get('/api/cards').json()['items'] == [{**saved, 'group_id': None}]
    with pytest.raises(IntegrityError):
        with app.state.auth.store.session() as db:
            db.get(OwnedCard, saved['id']).price = Decimal('-0.01')


@pytest.mark.parametrize('format,language', [('json', None), ('xlsx', None), ('xlsx', 'en'), ('xlsx', 'zh-CN')])
def test_price_export_import_round_trip(client, format, language):
    prices = [None, 0, 0.1, 12.34, 999999999999.99]
    for index, price in enumerate(prices):
        assert client.post('/api/cards', json={**CARD, 'card_number': str(index), 'price': price}).status_code == 201
    params = {'format': format}
    if language:
        params['language'] = language
    response = client.get('/api/cards/export', params=params)
    assert response.status_code == 200
    expected = list(reversed(prices))
    if format == 'json':
        assert [row['price'] for row in response.json()['items']] == expected
    else:
        workbook = load_workbook(BytesIO(response.content))
        try:
            rows = list(workbook.active.rows)
            heading = {'en': 'Price', 'zh-CN': '价格'}.get(language, 'price')
            column = [cell.value for cell in rows[0]].index(heading)
            assert [row[column].value for row in rows[1:]] == expected
            assert all(row[column].data_type == 'n' for row in rows[1:])
        finally:
            workbook.close()
    media_type = 'application/json' if format == 'json' else XLSX_MEDIA_TYPE
    login(client, 'bob')
    assert client.post(f'/api/cards/import?format={format}', content=response.content,
                       headers={'Content-Type': media_type}).json() == {'imported': len(prices), 'skipped': 0}
    imported = client.get('/api/cards').json()['items']
    assert {row['card_number']: row['price'] for row in imported} == {str(i): price for i, price in enumerate(prices)}


def test_price_import_is_atomic_and_duplicates_keep_price(client):
    path = '/api/cards/import?format=json'
    assert client.post(path, json={'items': [CARD, {**CARD, 'card_number': 'Other', 'price': -1}]}).status_code == 422
    assert client.get('/api/cards').json() == {'items': []}
    saved = client.post('/api/cards', json={**CARD, 'price': 10.01}).json()
    assert client.post(path, json={'items': [{**CARD, 'price': 99}, {**CARD, 'card_number': 'Other'}]}).json() == {
        'imported': 1, 'skipped': 1}
    assert {row['card_number']: row['price'] for row in client.get('/api/cards').json()['items']} == {
        saved['card_number']: 10.01, 'Other': None}
    assert client.get('/api/cards/export', params={'format': 'json', 'fields': 'price'}).json() == {
        'items': [{'price': None}, {'price': 10.01}]}
