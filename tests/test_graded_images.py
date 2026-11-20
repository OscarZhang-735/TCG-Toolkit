"""Card outline detection and the upload / crop / restore flow for graded card photos."""
from io import BytesIO

import cv2
import numpy as np
import pytest
from PIL import Image

from auth_helpers import register_user
from card_photos import _background, corner_error, photo
from test_graded_cards import PATH, add, client  # noqa: F401  (fixture)
from tcg_toolkit.collection import card_detect, card_enhance, graded_cards

BUCKET = 'https://cards.example.com'


@pytest.mark.parametrize('kind', ['card', 'slab'])
@pytest.mark.parametrize('background', ['dark', 'light', 'busy'])
@pytest.mark.parametrize('angle,tilt,size,seed', [
    (0, 0, (1200, 1600), 1), (8, .06, (1600, 1200), 2), (-15, .1, (1200, 1600), 3), (40, .08, (1200, 1600), 5)])
def test_detects_the_outline_on_varied_photos(kind, background, angle, tilt, size, seed):
    data, truth = photo(kind, background, angle, tilt, size, seed)
    found = card_detect.detect_card(card_detect.load_image(data))
    assert found is not None
    assert corner_error(found, truth) < 0.04


@pytest.mark.parametrize('background', ['dark', 'light', 'busy'])
def test_nothing_is_detected_without_a_card(background):
    output = BytesIO()
    Image.fromarray(_background(np.random.default_rng(3), (1200, 1600), background)).save(output, 'JPEG')
    assert card_detect.detect_card(card_detect.load_image(output.getvalue())) is None


def test_phone_rotation_is_applied_before_detection():
    data, truth = photo('card', 'dark', 5, .04, (1200, 1600), 9)
    image = Image.open(BytesIO(data))
    # Stored sideways with an EXIF "rotate 90 CW to display" tag, as phones do.
    exif = image.getexif(); exif[0x0112] = 6
    output = BytesIO(); image.transpose(Image.Transpose.ROTATE_90).save(output, 'JPEG', exif=exif)
    loaded = card_detect.load_image(output.getvalue())
    assert loaded.shape[:2] == (1200, 1600)
    assert corner_error(card_detect.detect_card(loaded), truth) < 0.04


def test_crop_is_upright_and_keeps_the_card_proportions():
    data, truth = photo('card', 'light', 12, .08, (1200, 1600), 4)
    webp, (width, height) = card_detect.crop_card(card_detect.load_image(data), list(reversed(truth)))
    assert abs(width / height - 630 / 880) < 0.04
    with Image.open(BytesIO(webp)) as image:
        assert image.format == 'WEBP' and image.size == (width, height)
    with pytest.raises(card_detect.ImageError):
        card_detect.crop_card(card_detect.load_image(data), [[5, 5], [10, 5], [10, 10], [5, 10]])


@pytest.mark.parametrize('content,code', [(b'not an image', 'graded_image_invalid'),
                                          (None, 'graded_image_dimensions')])
def test_unreadable_or_huge_images_are_refused(content, code):
    if content is None:
        output = BytesIO(); Image.new('RGB', (6001, 10)).save(output, 'PNG'); content = output.getvalue()
    with pytest.raises(card_detect.ImageError) as error:
        card_detect.load_image(content)
    assert error.value.code == code


def test_untagged_provider_images_fill_both_sides_and_uploads_replace_one():
    official = [{'url': 'https://a/1.jpg', 'alt': '1', 'side': None, 'thumbnail_url': None},
                {'url': 'https://a/2.jpg', 'alt': '2', 'side': None, 'thumbnail_url': None}]
    images = graded_cards.slab_images(official, {'front': None, 'back': None})
    assert [(image['url'], image['side'], image['uploaded']) for image in images] == [
        ('https://a/1.jpg', 'front', False), ('https://a/2.jpg', 'back', False)]
    images = graded_cards.slab_images(official, {'front': 'https://cos/x.webp', 'back': None})
    assert [(image['url'], image['side'], image['uploaded']) for image in images] == [
        ('https://cos/x.webp', 'front', True), ('https://a/1.jpg', 'back', False)]
    assert graded_cards.slab_images([], {'front': None, 'back': 'https://cos/y.webp'})[0]['side'] == 'back'


@pytest.fixture
def cos(monkeypatch):
    """In-memory COS bucket: uploads by URL and deletions."""
    monkeypatch.setenv('TCG_AVATAR_COS_URL', BUCKET)
    objects, deleted = {}, []

    class Reply:
        def __init__(self, status=200):
            self.status_code = status

        def raise_for_status(self):
            pass

    def put(url, content, **kwargs):
        assert kwargs['headers']['Content-Type'] == 'image/webp'
        objects[url] = content
        return Reply()

    def delete(url, **kwargs):
        deleted.append(url)
        objects.pop(url, None)
        return Reply(204)

    monkeypatch.setattr('tcg_toolkit.media.objects.httpx.put', put)
    monkeypatch.setattr('tcg_toolkit.media.objects.httpx.delete', delete)
    return objects, deleted


def upload(client, card_id, side, data, corners, content_type='image/jpeg'):  # noqa: F811
    text = ','.join(str(value) for point in corners for value in point)
    return client.put(f'{PATH}/{card_id}/images/{side}', params={'corners': text}, content=data,
                      headers={'Content-Type': content_type})


def test_detect_endpoint_suggests_corners(client):  # noqa: F811
    data, truth = photo('slab', 'busy', 10, .05, (1600, 1200), 11)
    response = client.post(f'{PATH}/detect-card', content=data, headers={'Content-Type': 'image/jpeg'})
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    body = response.json()
    assert (body['width'], body['height']) == (1200, 1600)
    assert corner_error(body['corners'], truth) < 0.04
    output = BytesIO(); Image.fromarray(_background(np.random.default_rng(1), (600, 800), 'dark')).save(output, 'PNG')
    assert client.post(f'{PATH}/detect-card', content=output.getvalue(),
                       headers={'Content-Type': 'image/png'}).json()['corners'] is None
    assert client.post(f'{PATH}/detect-card', content=b'x', headers={'Content-Type': 'text/plain'}).status_code == 415
    assert client.post(f'{PATH}/detect-card', content=b'x', headers={'Content-Type': 'image/png'}).json() == {
        'detail': 'graded_image_invalid'}
    assert client.post(f'{PATH}/detect-card', content=data, headers={'Content-Type': 'image/jpeg',
                                                                       'Origin': 'https://evil.test'}).status_code == 403


def test_upload_replaces_one_side_survives_refresh_and_restores(client, cos):  # noqa: F811
    objects, deleted = cos
    card = add(client).json()
    data, truth = photo('slab', 'dark', -6, .05, (1600, 1200), 12)
    response = upload(client, card['id'], 'back', data, truth)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    images = {image['side']: image for image in response.json()['images']}
    first = images['back']['url']
    assert first.startswith(BUCKET + '/tcgtoolkit/graded/') and images['back']['uploaded']
    assert images['front'] == {'url': 'https://example.test/front.jpg', 'alt': 'Front', 'side': 'front',
                               'thumbnail_url': None, 'uploaded': False}
    with Image.open(BytesIO(objects[first])) as stored:
        assert stored.format == 'WEBP' and abs(stored.width / stored.height - 760 / 1260) < 0.04
    # A lookup refresh never replaces the user's photo.
    assert client.post(f"{PATH}/{card['id']}/refresh", json={}).json()['images'] == response.json()['images']
    second = upload(client, card['id'], 'back', data, truth).json()
    assert second['images'][1]['url'] != first and deleted == [first]
    restored = client.request('DELETE', f"{PATH}/{card['id']}/images/back", json={}).json()
    assert [image['side'] for image in restored['images']] == ['front']
    assert deleted[-1] == second['images'][1]['url'] and not objects
    upload(client, card['id'], 'front', data, truth)
    assert client.request('DELETE', f"{PATH}/{card['id']}", json={}).status_code == 204
    assert not objects, 'deleting the card removes its uploaded photos'


def test_upload_validation_ownership_and_storage(client, cos, monkeypatch):  # noqa: F811
    objects, _ = cos
    card = add(client).json()
    data, truth = photo('card', 'dark', 0, 0, (1200, 1600), 13)
    for corners in ([[0, 0], [1, 1]], [[-50, 0], [10, 0], [10, 10], [0, 10]], [[0, 0], [4000, 0], [4000, 9], [0, 9]]):
        response = upload(client, card['id'], 'front', data, corners)
        assert response.status_code == 422 and response.json()['detail'] == 'graded_image_crop'
    assert upload(client, card['id'], 'side', data, truth).status_code == 404
    assert upload(client, 'missing', 'front', data, truth).json() == {'detail': 'graded_card_not_found'}
    assert upload(client, card['id'], 'front', data, truth, 'text/plain').status_code == 415
    monkeypatch.setattr(graded_cards, 'IMAGE_LIMIT', 1000)
    assert upload(client, card['id'], 'front', data, truth).json() == {'detail': 'graded_image_too_large'}
    monkeypatch.setattr(graded_cards, 'IMAGE_LIMIT', 12 * 1024 * 1024)
    assert not objects
    monkeypatch.setenv('TCG_AVATAR_COS_URL', '')
    response = upload(client, card['id'], 'front', data, truth)
    assert response.status_code == 503 and response.json()['detail'] == 'graded_image_storage_failed'
    monkeypatch.setenv('TCG_AVATAR_COS_URL', BUCKET)
    # Another account cannot touch this card, and nothing is uploaded for it.
    client.headers['Authorization'] = 'Bearer ' + register_user(client, 'bob').json()['token']
    assert upload(client, card['id'], 'front', data, truth).json() == {'detail': 'graded_card_not_found'}
    assert client.request('DELETE', f"{PATH}/{card['id']}/images/front", json={}).status_code == 404
    assert not objects


def glare_scene():
    """A card on a dark table: white top half with print, purple artwork with print below,
    and a bright glare veil washing over the artwork (as on photographed slabs)."""
    scene = np.full((1200, 900, 3), 45, np.uint8)
    scene[150:1050, 200:700] = (205, 202, 195)  # diffuse white: well below specular glare
    scene[600:1000, 220:680] = (110, 70, 140)
    for row in range(200, 1000, 40):
        scene[row:row + 6, 240:660] = (40, 30, 50) if row < 600 else (235, 215, 120)  # print, gold lines
    veil = np.zeros((1200, 900), np.float32)
    veil[650:950, 250:650] = 1.0
    veil = cv2.GaussianBlur(veil, (0, 0), 25)[:, :, None]
    glared = np.clip(scene * (1 - 0.9 * veil) + 255 * 0.9 * veil, 0, 255).astype(np.uint8)
    return scene, glared


def contrast(region):
    return float(np.percentile(region, 95) - np.percentile(region, 5))


def test_calibration_lifts_the_glare_veil_and_keeps_the_rest():
    scene, glared = glare_scene()
    result = card_enhance.calibrate(glared)
    assert result.shape == glared.shape and result.dtype == np.uint8
    lightness = lambda image: cv2.cvtColor(image, cv2.COLOR_RGB2LAB)[:, :, 0].astype(np.float32)
    before, after = lightness(glared), lightness(result)
    artwork = (slice(732, 755), slice(320, 580))  # purple between the gold lines, under the veil
    assert after[artwork].mean() < before[artwork].mean() - 20, 'the veil over the artwork is lifted'
    veiled = (slice(720, 880), slice(300, 600))
    assert contrast(after[veiled]) > contrast(before[veiled]) * 1.5, 'the print under it reads better'
    clean = (slice(220, 520), slice(240, 660))
    assert abs(after[clean].mean() - before[clean].mean()) < 10, 'unaffected areas stay close'
    assert card_enhance.glare_mask(glared)[veiled].mean() > 0.5


def test_a_white_card_is_not_mistaken_for_glare():
    scene, _ = glare_scene()
    # Only the margins against the dark table may pick up a little.
    assert card_enhance.glare_mask(scene)[250:950, 260:640].mean() < 0.08


def test_calibrate_endpoint_returns_a_same_size_jpeg(client):  # noqa: F811
    _, glared = glare_scene()
    output = BytesIO(); Image.fromarray(glared).save(output, 'PNG')
    response = client.post(f'{PATH}/calibrate-image', content=output.getvalue(), headers={'Content-Type': 'image/png'})
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    assert response.headers['cache-control'] == 'no-store'
    with Image.open(BytesIO(response.content)) as image:
        assert image.format == 'JPEG' and image.size == (900, 1200)
    assert client.post(f'{PATH}/calibrate-image', content=b'x', headers={'Content-Type': 'text/plain'}).status_code == 415
    assert client.post(f'{PATH}/calibrate-image', content=output.getvalue(), headers={
        'Content-Type': 'image/png', 'Origin': 'https://evil.test'}).status_code == 403
