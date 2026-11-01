from auth_helpers import persisted_data
import json
from contextlib import contextmanager

import pytest

from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.web import app
from test_yuyutei_cart import client, upstream, BASE, CARD, SECRETS, ORIGIN


@pytest.mark.parametrize('method,path', [('POST', '/cart/query'), ('POST', '/cart'), ('DELETE', '/cart')])
def test_missing_credentials_no_query_or_cookie_fallback(client, upstream, method, path):
    client.cookies.set('laravel_session', SECRETS['cookie'])
    response = client.request(method, BASE + path, params=SECRETS, json=CARD if path == '/cart' else {})
    assert response.status_code == 422
    assert response.json() == {'detail': 'invalid_yuyutei_request'}
    assert not upstream[0]


@pytest.mark.parametrize('method,path', [('POST', '/cart/query'), ('POST', '/cart'), ('DELETE', '/cart')])
def test_extra_fields_do_not_echo_nested_credentials(client, upstream, method, path):
    response = client.request(method, BASE + path, json={
        **(CARD if path == '/cart' else {}), 'credentials': SECRETS, 'extra': 'private-input'})
    assert response.status_code == 422
    assert response.json() == {'detail': 'invalid_yuyutei_request'}
    assert not upstream[0]


def test_legacy_routes_cannot_store_or_return_credentials(client, upstream):
    for method in ['GET', 'PUT', 'DELETE']:
        response = client.request(method, BASE + '/credentials', json=SECRETS)
        assert response.status_code in (404, 405)
        assert SECRETS['cookie'] not in response.text
    assert client.get(BASE + '/cart').status_code in (404, 405)
    assert not upstream[0]


def test_idempotency_excludes_credentials_and_still_requires_valid_envelope(client, upstream):
    headers = {'Idempotency-Key': 'stateless-key'}
    first = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=headers)
    assert first.status_code == 200
    second = client.post(BASE + '/cart', json={**CARD, 'credentials': {
        'cookie': 'different-cookie', 'csrf_token': 'different-csrf'}}, headers=headers)
    assert second.status_code == 200 and second.json() == first.json()
    assert second.headers['idempotency-replayed'] == 'true'
    assert client.post(BASE + '/cart', json=CARD, headers=headers).status_code == 422
    assert sum(r.method == 'POST' for r in upstream[0]) == 1
    raw = persisted_data(app.state.auth.store)
    assert not any(value.encode() in raw for value in SECRETS.values())
