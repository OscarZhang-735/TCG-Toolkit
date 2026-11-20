"""Validate account avatars and replace the stored account image."""
from io import BytesIO

from fastapi import HTTPException
from PIL import Image, ImageOps

from ..persistence.models import AvatarCleanup, User
from ..media.objects import AVATAR_PREFIX, cleanup_image, image_bucket, is_stored_image, store_webp

AVATAR_LIMIT = 5 * 1024 * 1024
AVATAR_TYPES = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP'}


def upload_avatar(content, media_type):
    bucket = image_bucket()
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

    return store_webp(output.getvalue(), AVATAR_PREFIX, 'avatar_storage_failed')


def save_avatar(store, user_id, content, media_type):
    url = upload_avatar(content, media_type)
    with store.session() as db:
        user = db.get(User, user_id, with_for_update=True)
        previous = user.avatar_url
        user.avatar_url = url
        if previous and is_stored_image(previous, image_bucket()):
            db.add(AvatarCleanup(url=previous, next_attempt_at=0))
        else:
            previous = None
    if previous:
        cleanup_image(store, previous)
    return {"avatar_url": url}
