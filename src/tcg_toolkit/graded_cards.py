"""Saved graded card certificates; each row keeps the latest official lookup.

Lookups go through ``grading.lookup_certificate`` and can take minutes, so they always
run between two short sessions: the pool has four connections and none is held while
the provider answers. A lookup sent with a ``request_id`` can be cancelled through
``DELETE /lookups/{request_id}``; a cancelled lookup is never saved.

Users may also replace either side's image with their own photo: ``detect-card`` finds the
card or slab outline with OpenCV, and the upload is perspective-cropped to the four
corners the user confirmed, then stored as public WebP in the avatar COS bucket.
"""
import json
import re
from io import BytesIO
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from PIL import Image
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, StrictStr
from sqlalchemy import select, update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth.avatar import GRADED_PREFIX, cleanup_avatar, is_avatar_object, avatar_bucket, store_webp
from .auth.dependencies import current_user, require_trusted_origin, write_guard
from .card_detect import ImageError, crop_card, detect_card, load_image
from .card_enhance import calibrate
from .card_schemas import CardPriceInput, GroupName, GroupTarget, price_record
from .cards import lock_user
from .database import is_unique_violation
from .db_models import AvatarCleanup, GradedCard, GradedCardGroup
from .grading import LOOKUPS, lookup_certificate, normalize_request, present

router = APIRouter(prefix='/api/graded-cards')


REQUEST_ID = r'^[A-Za-z0-9-]{8,64}$'


class LookupInput(BaseModel):
    # Chosen by the browser so it can cancel the lookup before this request answers.
    request_id: str | None = Field(default=None, pattern=REQUEST_ID)


class GradedCardInput(LookupInput, CardPriceInput):
    provider: StrictStr
    cert_number: StrictStr
    group_id: str | None = Field(default=None, min_length=1)


def keep_result(token):
    if not token.finish():
        raise HTTPException(409, detail='grading_lookup_cancelled')


def store(request: Request):
    return request.app.state.auth.store


def snapshot(result):
    return json.dumps({key: result[key] for key in ('source_url', 'certificate', 'images')}, ensure_ascii=False)


SIDES = ('front', 'back')


def slab_images(images, uploaded):
    """Front then back, each {url, alt, side, thumbnail_url, uploaded}.

    Untagged provider images fill the sides in page order (as PCG-CN lists them); a
    user's photo replaces that side, so a later refresh never undoes it.
    """
    sides = {side: next((image for image in images if image.get('side') == side), None) for side in SIDES}
    untagged = iter(image for image in images if not image.get('side'))
    result = []
    for side in SIDES:
        if uploaded.get(side):
            result.append({'url': uploaded[side], 'alt': f'Uploaded {side}', 'side': side,
                           'thumbnail_url': None, 'uploaded': True})
            continue
        image = sides[side] or next(untagged, None)
        if image is not None:
            result.append({**image, 'side': side, 'uploaded': False})
    return result


def record(row: GradedCard):
    data = json.loads(row.snapshot)
    certificate = data.get('certificate') or {}
    title, fields, english = present(row.provider, certificate)
    images = slab_images(data.get('images') or [], {'front': row.front_image_url, 'back': row.back_image_url})
    return {'id': row.id, 'provider': row.provider, 'cert_number': row.cert_number,
            'source_url': data.get('source_url'), 'title': title,
            'grade': certificate.get('grade') or '', 'fields': fields, 'english_fields': english,
            'population': certificate.get('population') or {}, 'images': images,
            'group_id': row.group_id, 'created_at': row.created_at, 'refreshed_at': row.refreshed_at,
            **price_record(row)}


def owned(db: Session, user_id, card_id, *, lock=False):
    query = select(GradedCard).where(GradedCard.user_id == user_id, GradedCard.id == card_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, detail='graded_card_not_found')
    return row


def reject_duplicate(db: Session, user_id, provider, cert_number):
    card_id = db.scalar(select(GradedCard.id).where(
        GradedCard.user_id == user_id, GradedCard.provider == provider, GradedCard.cert_number == cert_number))
    if card_id:
        raise HTTPException(409, detail={'type': 'graded_card_duplicate', 'id': card_id})


# Groups: the card library's model, in a table of their own. Deleting a group keeps its
# cards, ungrouped. The user row lock orders group deletion against membership writes.

def owned_group(db: Session, user_id, group_id):
    row = db.scalar(select(GradedCardGroup).where(GradedCardGroup.user_id == user_id, GradedCardGroup.id == group_id))
    if row is None:
        raise HTTPException(404, detail='graded_group_not_found')
    return row


def group_record(row: GradedCardGroup):
    return {'id': row.id, 'name': row.name, 'created_at': row.created_at}


def require_available_name(db: Session, user_id, name, group_id=None):
    query = select(GradedCardGroup.id).where(GradedCardGroup.user_id == user_id, GradedCardGroup.name == name)
    if group_id is not None:
        query = query.where(GradedCardGroup.id != group_id)
    if db.scalar(query) is not None:
        raise HTTPException(409, detail='graded_group_name_taken')


@router.get('/groups')
def groups(request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        rows = db.scalars(select(GradedCardGroup).where(GradedCardGroup.user_id == user['id']).order_by(
            GradedCardGroup.created_at, GradedCardGroup.id))
        return {'items': [group_record(row) for row in rows]}


@router.post('/groups', status_code=201, dependencies=[Depends(write_guard)])
def create_group(body: GroupName, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        lock_user(db, user['id'])
        require_available_name(db, user['id'], body.name)
        row = GradedCardGroup(id=str(uuid.uuid4()), user_id=user['id'], name=body.name, created_at=int(time.time()))
        db.add(row)
        return group_record(row)


@router.put('/groups/{group_id}', dependencies=[Depends(write_guard)])
def rename_group(group_id: str, body: GroupName, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        lock_user(db, user['id'])
        row = owned_group(db, user['id'], group_id)
        require_available_name(db, user['id'], body.name, row.id)
        row.name = body.name
        return group_record(row)


@router.delete('/groups/{group_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove_group(group_id: str, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        lock_user(db, user['id'])
        row = owned_group(db, user['id'], group_id)
        db.execute(sql_update(GradedCard).where(GradedCard.user_id == user['id'], GradedCard.group_id == row.id)
                   .values(group_id=None))
        db.delete(row)


@router.get('')
def graded_cards(request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        rows = db.scalars(select(GradedCard).where(GradedCard.user_id == user['id']).order_by(
            GradedCard.created_at.desc(), GradedCard.sort_id.desc()))
        return {'items': [record(row) for row in rows]}


@router.post('', status_code=201, dependencies=[Depends(write_guard)])
def add(body: GradedCardInput, request: Request, user=Depends(current_user)):
    provider, cert_number = normalize_request(body.provider, body.cert_number)
    # A card already saved answers at once instead of after a slow lookup.
    with store(request).session() as db:
        reject_duplicate(db, user['id'], provider.id, cert_number)
        if body.group_id is not None:
            owned_group(db, user['id'], body.group_id)
    with LOOKUPS.track(user['id'], body.request_id) as token:
        result = lookup_certificate(provider.id, cert_number, user['id'])
        keep_result(token)
        now = int(time.time())
        with store(request).session() as db:
            lock_user(db, user['id'])
            # The lookup can take minutes: a group deleted meanwhile saves the card ungrouped
            # rather than losing the result.
            group_id = body.group_id
            if group_id is not None and db.scalar(select(GradedCardGroup.id).where(
                    GradedCardGroup.user_id == user['id'], GradedCardGroup.id == group_id)) is None:
                group_id = None
            row = GradedCard(id=str(uuid.uuid4()), user_id=user['id'], provider=provider.id,
                             cert_number=cert_number, snapshot=snapshot(result), created_at=now, refreshed_at=now,
                             price=body.price, currency=body.currency, group_id=group_id)
            try:
                with db.begin_nested():
                    db.add(row)
                    db.flush()
            except IntegrityError as exc:
                # Another request saved the same certificate while this one was looking it up.
                if is_unique_violation(exc, 'graded_cards_user_cert_key'):
                    reject_duplicate(db, user['id'], provider.id, cert_number)
                raise
            return record(row)


@router.put('/{card_id}/group', dependencies=[Depends(write_guard)])
def move(card_id: str, body: GroupTarget, request: Request, user=Depends(current_user)):
    """Moves a card into a group; null takes it out of its group."""
    with store(request).session() as db:
        lock_user(db, user['id'])
        group_id = None if body.group_id is None else owned_group(db, user['id'], body.group_id).id
        row = owned(db, user['id'], card_id, lock=True)
        row.group_id = group_id
        return record(row)


@router.put('/{card_id}/price', dependencies=[Depends(write_guard)])
def update_price(card_id: str, request: Request, body: CardPriceInput, user=Depends(current_user)):
    with store(request).session() as db:
        row = owned(db, user['id'], card_id, lock=True)
        row.price, row.currency = body.price, body.currency
        return price_record(row)


@router.post('/{card_id}/refresh', dependencies=[Depends(write_guard)])
def refresh(card_id: str, request: Request, body: LookupInput, user=Depends(current_user)):
    with store(request).session() as db:
        row = owned(db, user['id'], card_id)
        provider, cert_number = row.provider, row.cert_number
    # A failed or cancelled lookup raises before the write, so the saved snapshot stays.
    with LOOKUPS.track(user['id'], body.request_id) as token:
        result = lookup_certificate(provider, cert_number, user['id'])
        keep_result(token)
        with store(request).session() as db:
            row = owned(db, user['id'], card_id, lock=True)
            row.snapshot, row.refreshed_at = snapshot(result), int(time.time())
            return record(row)


@router.delete('/lookups/{request_id}', status_code=204, dependencies=[Depends(write_guard)])
def cancel_lookup(request_id: str, user=Depends(current_user)):
    """Stops this account's lookup; 409 when it already finished and was saved."""
    if not re.fullmatch(REQUEST_ID, request_id):
        raise HTTPException(422, detail='invalid_request_id')
    if LOOKUPS.cancel(user['id'], request_id) == 'finished':
        raise HTTPException(409, detail='graded_lookup_finished')


@router.delete('/{card_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove(card_id: str, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        row = owned(db, user['id'], card_id, lock=True)
        stale = queue_cleanup(db, row.front_image_url, row.back_image_url)
        db.delete(row)
    drain_cleanup(request, stale)


# Uploaded photos

IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
IMAGE_LIMIT = 12 * 1024 * 1024


def queue_cleanup(db: Session, *urls):
    """Queue our own COS objects for deletion in the caller's transaction."""
    bucket = avatar_bucket()
    stale = [url for url in urls if url and bucket is not None and is_avatar_object(url, bucket)]
    for url in stale:
        db.merge(AvatarCleanup(url=url, next_attempt_at=0))
    return stale


def drain_cleanup(request: Request, urls):
    # Best effort now; the background cleanup task retries anything left in the queue.
    for url in urls:
        cleanup_avatar(store(request), url)


async def read_image(request: Request, user):
    media_type = request.headers.get('content-type', '').split(';')[0].strip().lower()
    if media_type not in IMAGE_TYPES:
        raise HTTPException(415, detail='graded_image_invalid')
    request.app.state.auth_limiter.check(('graded-image', user['id']), 30)
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > IMAGE_LIMIT:
            raise HTTPException(413, detail='graded_image_too_large')
        content.extend(chunk)
    return bytes(content)


def decode(content):
    try:
        return load_image(content)
    except ImageError as exc:
        raise HTTPException(422, detail=exc.code) from None


@router.post('/calibrate-image', dependencies=[Depends(require_trusted_origin)])
async def calibrate_image(request: Request, user=Depends(current_user)):
    """Optional brightness calibration (glare, uneven light) of a photo before cropping.

    Returns a same-size JPEG; nothing is stored, so the browser can undo it by keeping
    the original.
    """
    content = await read_image(request, user)

    def run():
        output = BytesIO()
        Image.fromarray(calibrate(decode(content))).save(output, format='JPEG', quality=92)
        return output.getvalue()

    return Response(await run_in_threadpool(run), media_type='image/jpeg')


@router.post('/detect-card', dependencies=[Depends(require_trusted_origin)])
async def detect(request: Request, user=Depends(current_user)):
    """Suggested crop for a photo: {width, height, corners} with corners null if none found."""
    content = await read_image(request, user)

    def run():
        image = decode(content)
        return {'width': image.shape[1], 'height': image.shape[0], 'corners': detect_card(image)}

    return await run_in_threadpool(run)


def parse_corners(text, width, height):
    try:
        values = [float(value) for value in text.split(',')]
    except ValueError:
        values = []
    if len(values) != 8 or any(value != value for value in values):
        raise HTTPException(422, detail='graded_image_crop')
    # Handles may sit on the photo edge; allow a pixel of rounding, never outside.
    if any(not -1 <= x <= width for x in values[0::2]) or any(not -1 <= y <= height for y in values[1::2]):
        raise HTTPException(422, detail='graded_image_crop')
    return [values[i:i + 2] for i in range(0, 8, 2)]


@router.put('/{card_id}/images/{side}', dependencies=[Depends(require_trusted_origin)])
async def upload_image(card_id: str, side: str, request: Request, corners: str = Query(max_length=200),
                       user=Depends(current_user)):
    """Crops the photo to the confirmed corners and makes it this side's image."""
    if side not in SIDES:
        raise HTTPException(404, detail='graded_image_side')
    content = await read_image(request, user)

    def run():
        with store(request).session() as db:
            owned(db, user['id'], card_id)
        image = decode(content)
        try:
            data, _ = crop_card(image, parse_corners(corners, image.shape[1], image.shape[0]))
        except ImageError as exc:
            raise HTTPException(422, detail=exc.code) from None
        url = store_webp(data, GRADED_PREFIX, 'graded_image_storage_failed')
        with store(request).session() as db:
            row = db.scalar(select(GradedCard).where(GradedCard.user_id == user['id'], GradedCard.id == card_id)
                            .with_for_update())
            if row is None:
                # Deleted while uploading: the new object is orphaned, so queue it instead.
                stale = queue_cleanup(db, url)
            else:
                column = f'{side}_image_url'
                stale = queue_cleanup(db, getattr(row, column))
                setattr(row, column, url)
                result = record(row)
        drain_cleanup(request, stale)
        if row is None:
            raise HTTPException(404, detail='graded_card_not_found')
        return result

    return await run_in_threadpool(run)


@router.delete('/{card_id}/images/{side}', dependencies=[Depends(write_guard)])
def restore_image(card_id: str, side: str, request: Request, user=Depends(current_user)):
    """Drops the uploaded photo; the provider's image (or a placeholder) shows again."""
    if side not in SIDES:
        raise HTTPException(404, detail='graded_image_side')
    with store(request).session() as db:
        row = owned(db, user['id'], card_id, lock=True)
        column = f'{side}_image_url'
        stale = queue_cleanup(db, getattr(row, column))
        setattr(row, column, None)
        result = record(row)
    drain_cleanup(request, stale)
    return result
