"""Hobby Station card image URLs shared by search and favorites."""
from urllib.parse import quote, unquote, urlsplit

from .identity import HOBBY_GAME_DIRS

HOBBY_ORIGIN = "https://www.hobbystation-single.jp"
HOBBY_IMAGE_ROOT = "/html/upload/save_image/"
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def valid_image(game_dir: str, filename: str) -> bool:
    return (game_dir in HOBBY_GAME_DIRS.values() and "/" not in filename and "\\" not in filename
            and filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")))


def public_hobby_image_url(request, image_url):
    if image_url and image_url.startswith(HOBBY_ORIGIN + HOBBY_IMAGE_ROOT):
        path = unquote(urlsplit(image_url).path.removeprefix(HOBBY_IMAGE_ROOT))
        game_dir, _, filename = path.partition("/")
        if valid_image(game_dir, filename):
            return str(request.url_for("hobby_card_image", game_dir=game_dir, filename=quote(filename, safe="")))
    return image_url
