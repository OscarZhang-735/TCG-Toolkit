"""Synthetic phone photos of cards and slabs with known corners, for detection tests."""
from io import BytesIO

import cv2
import numpy as np
from PIL import Image


def _card(rng, width=630, height=880):
    card = np.full((height, width, 3), 245, np.uint8)
    art = rng.integers(40, 220, (3,))
    cv2.rectangle(card, (40, 90), (width - 40, height - 300), tuple(int(v) for v in art), -1)
    for _ in range(12):  # artwork detail, so edges inside the card compete with its outline
        centre = (int(rng.integers(60, width - 60)), int(rng.integers(110, height - 320)))
        cv2.circle(card, centre, int(rng.integers(10, 60)), tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
    cv2.rectangle(card, (40, height - 280), (width - 40, height - 60), (230, 225, 210), -1)
    for row in range(5):
        cv2.line(card, (60, height - 250 + row * 40), (width - 80, height - 250 + row * 40), (60, 60, 60), 3)
    cv2.rectangle(card, (0, 0), (width - 1, height - 1), (30, 30, 30), 12)  # printed black border
    return card


def _slab(rng):
    """Clear holder with a label on top and the card inside (about 0.6 aspect)."""
    width, height = 760, 1260
    slab = np.full((height, width, 3), 214, np.uint8)
    cv2.rectangle(slab, (30, 30), (width - 31, 250), (250, 250, 250), -1)  # label
    cv2.rectangle(slab, (50, 50), (300, 120), (200, 30, 40), -1)
    cv2.putText(slab, 'GEM MT 10', (330, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (20, 20, 20), 4)
    card = _card(rng)
    slab[320:320 + card.shape[0], 65:65 + card.shape[1]] = card
    cv2.rectangle(slab, (0, 0), (width - 1, height - 1), (150, 150, 150), 10)
    return slab


def _background(rng, size, style):
    height, width = size
    if style == 'dark':
        base = np.full((height, width, 3), 45, np.float32)
    elif style == 'light':
        base = np.full((height, width, 3), 200, np.float32)
    else:  # busy: wood-like stripes
        stripes = (np.sin(np.linspace(0, 40, width))[None, :] * 35 + 120).astype(np.float32)
        base = np.dstack([stripes + 30, stripes, stripes - 30]).repeat(height, axis=0)
    gradient = np.linspace(-25, 25, height, dtype=np.float32)[:, None, None]
    noise = rng.normal(0, 9, (height, width, 3)).astype(np.float32)
    return np.clip(base + gradient + noise, 0, 255).astype(np.uint8)


def photo(kind='card', background='dark', angle=8.0, tilt=0.06, size=(1200, 1600), seed=1):
    """(jpeg_bytes, true_corners) with corners ordered TL, TR, BR, BL."""
    rng = np.random.default_rng(seed)
    item = _slab(rng) if kind == 'slab' else _card(rng)
    height, width = size
    scene = _background(rng, size, background)
    h, w = item.shape[:2]
    scale = 0.62 * min(height / h, width / w)
    cx, cy = width / 2 + rng.uniform(-40, 40), height / 2 + rng.uniform(-40, 40)
    box = np.array([[-w, -h], [w, -h], [w, h], [-w, h]], np.float32) * scale / 2
    theta = np.deg2rad(angle)
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]], np.float32)
    corners = box @ rotation.T + [cx, cy]
    corners[0] += [w * scale * tilt, 0]  # perspective: top edge narrower, as seen from below
    corners[1] -= [w * scale * tilt, 0]
    source = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    matrix = cv2.getPerspectiveTransform(source, corners.astype(np.float32))
    warped = cv2.warpPerspective(item, matrix, (width, height))
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), matrix, (width, height))
    scene[mask > 0] = warped[mask > 0]
    glare = np.zeros((height, width), np.float32)
    cv2.circle(glare, (int(cx + 60), int(cy - 120)), 90, 1.0, -1)
    glare = cv2.GaussianBlur(glare, (0, 0), 40)[:, :, None]
    scene = np.clip(scene * (1 - 0.35 * glare) + 255 * 0.35 * glare, 0, 255).astype(np.uint8)
    scene = cv2.GaussianBlur(scene, (3, 3), 0)
    output = BytesIO()
    Image.fromarray(scene).save(output, format='JPEG', quality=82)
    return output.getvalue(), corners.tolist()


def corner_error(found, truth):
    """Largest corner distance as a share of the true outline's longer side."""
    found, truth = np.asarray(found), np.asarray(truth)
    side = max(np.linalg.norm(truth[1] - truth[0]), np.linalg.norm(truth[3] - truth[0]))
    # Which corner counts as "top-left" is ambiguous for a steeply rotated card.
    return min(float(np.max(np.linalg.norm(np.roll(found, shift, axis=0) - truth, axis=1)) / side)
               for shift in range(4))
