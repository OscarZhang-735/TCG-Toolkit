from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy.exc import IntegrityError

from tcg_toolkit.cards import XLSX_MEDIA_TYPE
from tcg_toolkit.db_models import OwnedCard
from tcg_toolkit.web import app
from test_cards import CARD, client, login


@pytest.mark.parametrize('sent,stored', [(None, 'JPY'), ('JPY', 'JPY'), ('CNY', 'CNY'), (' cad ', 'CAD')])
def test_currency_defaults_to_jpy_and_is_normalized(client, sent, stored):
    body = {**CARD, 'price': 12.5} | ({} if sent is None else {'currency': sent})
    saved = client.post('/api/cards', json=body).json()
    assert saved['currency'] == stored and saved['price'] == 12.5
    assert client.get('/api/cards').json()['items'] == [saved]


def test_currency_update_omission_and_validation(client):
    saved = client.post('/api/cards', json={**CARD, 'price': 30, 'currency': 'CNY'}).json()
    path = f"/api/cards/{saved['id']}"
    # Older clients send neither price nor currency: both are kept.
    kept = client.put(path, json={**CARD, 'quantity': 5}).json()
    assert (kept['quantity'], kept['price'], kept['currency']) == (5, 30, 'CNY')
    assert client.put(path, json={**CARD, 'price': 30, 'currency': 'cad'}).json()['currency'] == 'CAD'
    for bad in ['USD', '', 1, True, ['JPY']]:
        assert client.post('/api/cards', json={**CARD, 'card_number': 'X', 'currency': bad}).status_code == 422
        assert client.put(path, json={**CARD, 'currency': bad}).status_code == 422
    assert client.get('/api/cards').json()['items'][0]['currency'] == 'CAD'
    # Moving between groups keeps the currency.
    group = client.post('/api/cards/groups', json={'name': 'Deck'}).json()
    assert client.put(f'{path}/group', json={'group_id': group['id']}).json()['currency'] == 'CAD'
    with pytest.raises(IntegrityError):
        with app.state.auth.store.session() as db:
            db.get(OwnedCard, saved['id']).currency = 'USD'


@pytest.mark.parametrize('format,language', [('json', None), ('xlsx', None), ('xlsx', 'zh-CN')])
def test_currency_export_import_round_trip(client, format, language):
    rows = [(None, 'JPY'), (35, 'CNY'), (12.5, 'CAD'), (800, 'JPY')]
    for index, (price, currency) in enumerate(rows):
        client.post('/api/cards', json={**CARD, 'card_number': str(index), 'price': price, 'currency': currency})
    params = {'format': format} | ({'language': language} if language else {})
    response = client.get('/api/cards/export', params=params)
    expected = [currency for _, currency in reversed(rows)]
    if format == 'json':
        assert [row['currency'] for row in response.json()['items']] == expected
    else:
        workbook = load_workbook(BytesIO(response.content))
        try:
            sheet = list(workbook.active.rows)
            column = [cell.value for cell in sheet[0]].index('币种' if language else 'currency')
            assert [row[column].value for row in sheet[1:]] == expected
        finally:
            workbook.close()
    media_type = 'application/json' if format == 'json' else XLSX_MEDIA_TYPE
    login(client, 'bob')
    assert client.post(f'/api/cards/import?format={format}', content=response.content,
                       headers={'Content-Type': media_type}).json() == {'imported': len(rows), 'skipped': 0}
    assert {row['card_number']: (row['price'], row['currency']) for row in client.get('/api/cards').json()['items']} == {
        str(index): row for index, row in enumerate(rows)}


def test_import_without_currency_defaults_to_jpy_and_rejects_unknown_codes(client):
    path = '/api/cards/import?format=json'
    response = client.post(path, json={'items': [{**CARD, 'price': 5}, {**CARD, 'card_number': 'B', 'currency': 'cny'}]})
    assert response.json() == {'imported': 2, 'skipped': 0}
    assert {row['card_number']: row['currency'] for row in client.get('/api/cards').json()['items']} == {
        CARD['card_number']: 'JPY', 'B': 'CNY'}
    response = client.post(path, json={'items': [{**CARD, 'card_number': 'C'}, {**CARD, 'card_number': 'D', 'currency': 'USD'}]})
    assert response.status_code == 422
    assert response.json()['detail'] == {'code': 'cards_import_row', 'row': 2}
    assert len(client.get('/api/cards').json()['items']) == 2
