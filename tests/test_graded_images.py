"""Card outline detection and the upload / crop / restore flow for graded card photos."""
from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image

from card_photos import corner_error, photo
from cos_bucket import BUCKET, cos, signed_object_url
from test_graded_cards import PATH, add, client  # noqa: F401  (fixture)
from tcg_toolkit.media.objects import GRADED_PREFIX, signed_image_url, store_webp
from tcg_toolkit.persistence.models import GradedCard


def upload(client, card_id, side, data, corners, content_type='image/jpeg'):  # noqa: F811
    text = ','.join(str(value) for point in corners for value in point)
    return client.put(f'{PATH}/{card_id}/images/{side}', params={'corners': text}, content=data,
                      headers={'Content-Type': content_type})


@pytest.mark.parametrize('missing', ['TCG_COS_SECRET_ID', 'TCG_COS_SECRET_KEY'])
def test_private_images_without_credentials(monkeypatch, cos, missing):
    monkeypatch.delenv(missing)
    monkeypatch.setattr(cos, 'send', lambda request: pytest.fail('Unexpected COS request'))
    stored = BUCKET + GRADED_PREFIX + 'b' * 32 + '.webp'
    external = 'https://example.test/front.jpg'
    assert signed_image_url(stored) is None
    assert signed_image_url(external) == external
    with pytest.raises(HTTPException) as exc:
        store_webp(b'photo', GRADED_PREFIX, 'graded_image_storage_failed')
    assert (exc.value.status_code, exc.value.detail) == (503, 'graded_image_storage_failed')


def test_detect_endpoint_suggests_corners(client):  # noqa: F811
    data, truth = photo('slab', 'busy', 10, .05, (1600, 1200), 11)
    response = client.post(f'{PATH}/detect-card', content=data, headers={'Content-Type': 'image/jpeg'})
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    body = response.json()
    assert (body['width'], body['height']) == (1200, 1600)
    assert corner_error(body['corners'], truth) < 0.04


def test_upload_replaces_one_side_survives_refresh_and_restores(client, cos):  # noqa: F811
    card = add(client).json()
    data, truth = photo('slab', 'dark', -6, .05, (1600, 1200), 12)
    response = upload(client, card['id'], 'back', data, truth)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    images = {image['side']: image for image in response.json()['images']}
    first = signed_object_url(images['back']['url'])
    assert first.startswith(BUCKET + '/tcgtoolkit/graded/') and images['back']['uploaded']
    assert images['front'] == {'url': 'https://example.test/front.jpg', 'alt': 'Front', 'side': 'front',
                               'thumbnail_url': None, 'uploaded': False}
    with client.app.state.database.session() as db:
        assert db.get(GradedCard, card['id']).back_image_url == first
    with Image.open(BytesIO(cos.objects[first])) as stored:
        assert stored.format == 'WEBP' and abs(stored.width / stored.height - 760 / 1260) < 0.04
    # A lookup refresh never replaces the user's photo.
    refreshed = client.post(f"{PATH}/{card['id']}/refresh", json={}).json()['images']
    assert refreshed[0] == images['front'] and signed_object_url(refreshed[1]['url']) == first
    second = signed_object_url(upload(client, card['id'], 'back', data, truth).json()['images'][1]['url'])
    assert second != first and cos.deleted == [first]
    restored = client.request('DELETE', f"{PATH}/{card['id']}/images/back", json={}).json()
    assert [image['side'] for image in restored['images']] == ['front']
    assert cos.deleted[-1] == second and not cos.objects
    upload(client, card['id'], 'front', data, truth)
    assert client.request('DELETE', f"{PATH}/{card['id']}", json={}).status_code == 204
    assert not cos.objects, 'deleting the card removes its uploaded photos'
