"""Validate account avatars and store them as public COS image objects."""
import asyncio
from io import BytesIO
import logging
import os
import re
import time
from uuid import uuid4

import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..db_models import AvatarCleanup, User

AVATAR_LIMIT = 5 * 1024 * 1024
AVATAR_TYPES = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP'}
AVATAR_PREFIX = '/tcgtoolkit/avatars/'
CLEANUP_INTERVAL = 60
logger = logging.getLogger(__name__)


def avatar_bucket():
    try:
        bucket = httpx.URL(os.getenv('TCG_AVATAR_COS_URL', '').strip())
    except httpx.InvalidURL:
        return None
    if (bucket.scheme != 'https' or not bucket.host or bucket.userinfo
            or bucket.raw_path != b'/' or bucket.fragment):
        return None
    return bucket.copy_with(path='/')


def upload_avatar(content, media_type):
    bucket = avatar_bucket()
    if bucket is None:
        raise HTTPException(503, detail='avatar_storage_failed')

    output = BytesIO()
    try:
        with Image.open(BytesIO(content), formats=[AVATAR_TYPES[media_type]]) as image:
            if max(image.size) > 4096:
                raise HTTPException(422, detail='avatar_dimensions')
            avatar = ImageOps.exif_transpose(image).convert('RGBA')
            avatar.thumbnail((256, 256), Image.Resampling.LANCZOS)
            avatar.save(output, format='WEBP', quality=85)
    except Image.DecompressionBombError:
        raise HTTPException(422, detail='avatar_dimensions') from None
    except (OSError, ValueError, SyntaxError):
        raise HTTPException(422, detail='avatar_invalid') from None

    url = str(bucket.copy_with(path=f'{AVATAR_PREFIX}{uuid4().hex}.webp'))
    try:
        response = httpx.put(url, content=output.getvalue(), timeout=15,
                             headers={'Content-Type': 'image/webp'}, trust_env=False)
        response.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(502, detail='avatar_storage_failed') from None
    return url


def is_avatar_object(url, bucket):
    try:
        target = httpx.URL(url)
    except httpx.InvalidURL:
        return False
    return (target.copy_with(path='/') == bucket and not target.query and not target.fragment
            and re.fullmatch(rb'/tcgtoolkit/avatars/[0-9a-f]{32}\.webp', target.raw_path) is not None)


def save_avatar(store, user_id, content, media_type):
    url = upload_avatar(content, media_type)
    with store.session() as db:
        user = db.get(User, user_id, with_for_update=True)
        previous = user.avatar_url
        user.avatar_url = url
        if previous and is_avatar_object(previous, avatar_bucket()):
            db.add(AvatarCleanup(url=previous, next_attempt_at=0))
        else:
            previous = None
    if previous:
        cleanup_avatar(store, previous)
    return {"avatar_url": url}


def cleanup_avatar(store, url=None):
    bucket = avatar_bucket()
    if bucket is None:
        return False
    query = select(AvatarCleanup).where(AvatarCleanup.next_attempt_at <= int(time.time()))
    if url is not None:
        query = query.where(AvatarCleanup.url == url)
    query = query.order_by(AvatarCleanup.next_attempt_at, AvatarCleanup.url).limit(1).with_for_update(skip_locked=True)
    try:
        with store.session() as db:
            pending = db.scalar(query)
            if pending is None:
                return False
            try:
                if not is_avatar_object(pending.url, bucket):
                    raise ValueError('Avatar bucket changed')
                response = httpx.delete(pending.url, timeout=5, trust_env=False)
                if response.status_code != 404:
                    response.raise_for_status()
            except (httpx.HTTPError, ValueError):
                pending.next_attempt_at = int(time.time()) + CLEANUP_INTERVAL
                logger.warning('COS avatar deletion deferred')
            else:
                db.delete(pending)
    except SQLAlchemyError:
        logger.warning('Could not process the avatar cleanup queue')
        return False
    return True


async def retry_avatar_cleanup(store, stop):
    while not stop.is_set():
        if await asyncio.to_thread(cleanup_avatar, store):
            continue
        try:
            await asyncio.wait_for(stop.wait(), CLEANUP_INTERVAL)
        except TimeoutError:
            pass
