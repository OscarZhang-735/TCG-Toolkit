"""Find a card or slab outline in a photo and cut it out with OpenCV.

``detect_card`` returns the four corners (top-left, top-right, bottom-right, bottom-left,
in source pixels) of the most likely card-shaped quadrilateral, or ``None``.
``crop_card`` perspective-corrects any four corners into an upright WebP image.
Images are decoded with Pillow first so EXIF rotation from phone cameras is applied.
"""
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_SOURCE_SIDE = 6000      # refuse larger photos (decompression bombs, memory)
DETECT_SIDE = 900           # detection runs on a copy this size; corners are scaled back
OUTPUT_SIDE = 1600          # longest side of a saved crop
# Cards are 63 x 88 mm (0.72) and graded slabs roughly 0.55-0.65; allow some slack for
# perspective so slabs, raw cards and toploaders are all accepted.
ASPECT_RANGE = (0.45, 0.92)
MIN_AREA, MAX_AREA = 0.08, 0.985  # share of the photo the outline may cover


class ImageError(ValueError):
    """The upload is not a readable image of an acceptable size."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def load_image(content):
    """RGB array of the upload with EXIF orientation applied."""
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ImageError('graded_image_invalid')
            if max(image.size) > MAX_SOURCE_SIDE:
                raise ImageError('graded_image_dimensions')
            return np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
    except Image.DecompressionBombError:
        raise ImageError('graded_image_dimensions') from None
    except (OSError, SyntaxError):
        raise ImageError('graded_image_invalid') from None


def order_corners(points):
    """Top-left, top-right, bottom-right, bottom-left."""
    # Clockwise around the centre (image y points down), starting nearest the top-left.
    # Sorting by x+y alone breaks near 45 degree rotations and can cross the outline.
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    centre = points.mean(axis=0)
    points = points[np.argsort(np.arctan2(points[:, 1] - centre[1], points[:, 0] - centre[0]))]
    return np.roll(points, -int(np.argmin(points.sum(axis=1))), axis=0).astype(np.float32)


def _side_lengths(corners):
    tl, tr, br, bl = corners
    width = max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))
    height = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))
    return width, height


def _edge_maps(gray):
    """Several binary edge/region maps; photos differ in contrast, glare and background."""
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    median = float(np.median(blurred))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    for low, high in ((0.66 * median, 1.33 * median), (30, 90), (60, 160)):
        edges = cv2.Canny(blurred, max(0, low), min(255, max(high, low + 10)))
        yield cv2.morphologyEx(cv2.dilate(edges, kernel), cv2.MORPH_CLOSE, kernel, iterations=2)
    # Bright slab or card on a darker (or darker on lighter) background.
    _, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    yield otsu
    yield 255 - otsu


def _edge_support(magnitude, corners):
    """Weakest side's edge evidence (0-1), relative to the photo's strong edges.

    Each side scores its mean gradient strength and the share of its length that lies
    on a clear edge. A real outline is a step along all four sides; a background region,
    the picture frame or a plastic sleeve fold usually lacks one or only touches it.
    """
    reference = float(np.percentile(magnitude, 95)) or 1.0
    height, width = magnitude.shape
    weakest = 1.0
    for start, end in zip(corners, np.roll(corners, -1, axis=0)):
        steps = np.linspace(0.05, 0.95, 40)[:, None]
        points = start + (end - start) * steps
        xs = points[:, 0].round().astype(int).clip(0, width - 1)
        ys = points[:, 1].round().astype(int).clip(0, height - 1)
        values = magnitude[ys, xs]
        strength = min(1.0, float(values.mean()) / reference)
        coverage = float((values > 0.5 * reference).mean())
        weakest = min(weakest, 0.5 * strength + 0.5 * coverage)
    return weakest


def _plausible_shape(corners):
    """A card seen in perspective: near-right corners and opposite sides of similar length.

    Rejects outlines stitched from different objects, e.g. a slab's top edge joined to
    the card's bottom edge (seen on real photos with glare breaking the slab outline).
    """
    for index in range(4):
        before, at, after = corners[index - 1], corners[index], corners[(index + 1) % 4]
        first, second = before - at, after - at
        cosine = float(np.dot(first, second) / (np.linalg.norm(first) * np.linalg.norm(second) or 1))
        if not 72 <= np.degrees(np.arccos(np.clip(cosine, -1, 1))) <= 108:
            return False
    sides = [np.linalg.norm(corners[(index + 1) % 4] - corners[index]) for index in range(4)]
    return all(min(sides[i], sides[i + 2]) / max(sides[i], sides[i + 2]) >= 0.72 for i in (0, 1))


def _touches_frame(corners, shape):
    """Two or more corners on the photo border: the frame itself, not an object in it."""
    height, width = shape
    margin = 0.015 * max(height, width)
    near = ((corners[:, 0] < margin) | (corners[:, 0] > width - 1 - margin)
            | (corners[:, 1] < margin) | (corners[:, 1] > height - 1 - margin))
    return int(near.sum()) >= 2


def _candidates(binary, area_total, magnitude):
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        hull = cv2.convexHull(contour)
        area = cv2.contourArea(hull)
        if not MIN_AREA * area_total <= area <= MAX_AREA * area_total:
            continue
        perimeter = cv2.arcLength(hull, True)
        quad = None
        for epsilon in (0.015, 0.02, 0.03, 0.045):
            approx = cv2.approxPolyDP(hull, epsilon * perimeter, True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                quad = approx.reshape(4, 2)
                break
        if quad is None:
            # Rounded slab corners or a broken edge: accept a well-filled rotated box.
            rect = cv2.minAreaRect(hull)
            box_area = rect[1][0] * rect[1][1]
            if not box_area or area / box_area < 0.9:
                continue
            quad = cv2.boxPoints(rect)
        corners = order_corners(quad)
        scored = _score(corners, area_total, magnitude, binary.shape)
        if scored is not None:
            yield corners, *scored


def _score(corners, area_total, magnitude, shape):
    """(score, support) for an outline, or None when it cannot be a card or slab."""
    width, height = _side_lengths(corners)
    if not width or not height or _touches_frame(corners, shape) or not _plausible_shape(corners):
        return None
    area = cv2.contourArea(corners)
    aspect = min(width, height) / max(width, height)
    if not (MIN_AREA * area_total <= area <= MAX_AREA * area_total and ASPECT_RANGE[0] <= aspect <= ASPECT_RANGE[1]):
        return None
    # How rectangular the outline is: its area against the box its sides span.
    fill = area / (width * height)
    support = _edge_support(magnitude, corners)
    if fill < 0.8 or support < 0.2:
        return None
    # Bigger, squarer outlines with clear edges win; the slab beats the card inside it.
    return area / area_total * fill * (0.6 + 0.4 * support), support


def _intersect(first, second):
    (x1, y1, x2, y2), (x3, y3, x4, y4) = first, second
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) < 1e-6:
        return None
    a, b = x1 * y2 - y1 * x2, x3 * y4 - y3 * x4
    return np.array([(a * (x3 - x4) - (x1 - x2) * b) / denominator,
                     (a * (y3 - y4) - (y1 - y2) * b) / denominator], dtype=np.float32)


def _line_candidates(gray, area_total, magnitude):
    """Outlines assembled from straight edges.

    Glare, reflections and the plastic sleeve break a slab's outline into pieces, so it
    rarely closes as one contour; its four sides still show up as long straight segments.
    """
    height, width = gray.shape
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 50, minLineLength=0.18 * min(height, width), maxLineGap=20)
    if lines is None:
        return
    groups = {'horizontal': [], 'vertical': []}
    for x1, y1, x2, y2 in lines[:, 0].astype(np.float32):
        angle = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 180
        kind = 'horizontal' if angle < 25 or angle > 155 else 'vertical' if 65 < angle < 115 else None
        if kind is None:
            continue
        # Position across the image: y at the centre column for horizontal lines, x for vertical.
        if kind == 'horizontal':
            position = y1 + (y2 - y1) * ((width / 2 - x1) / ((x2 - x1) or 1))
        else:
            position = x1 + (x2 - x1) * ((height / 2 - y1) / ((y2 - y1) or 1))
        groups[kind].append((float(np.hypot(x2 - x1, y2 - y1)), float(position), (x1, y1, x2, y2)))
    picked = {}
    for kind, items in groups.items():
        kept = []
        for length, position, line in sorted(items, reverse=True):  # longest first; drop near duplicates
            if all(abs(position - other) > 0.02 * (height if kind == 'horizontal' else width) for _, other, _ in kept):
                kept.append((length, position, line))
            if len(kept) == 14:
                break
        picked[kind] = sorted(kept, key=lambda item: item[1])
    horizontal, vertical = picked['horizontal'], picked['vertical']
    for i, top in enumerate(horizontal):
        for bottom in horizontal[i + 1:]:
            if bottom[1] - top[1] < 0.25 * height:
                continue
            for j, left in enumerate(vertical):
                for right in vertical[j + 1:]:
                    if right[1] - left[1] < 0.2 * width:
                        continue
                    points = [_intersect(top[2], left[2]), _intersect(top[2], right[2]),
                              _intersect(bottom[2], right[2]), _intersect(bottom[2], left[2])]
                    if any(point is None for point in points):
                        continue
                    corners = np.array(points, dtype=np.float32)
                    if (corners[:, 0].min() < -2 or corners[:, 1].min() < -2
                            or corners[:, 0].max() > width + 1 or corners[:, 1].max() > height + 1):
                        continue
                    scored = _score(corners, area_total, magnitude, gray.shape)
                    if scored is not None:
                        yield corners, *scored


def detect_card(image):
    """Corners of the card or slab as [[x, y], ...] in source pixels, or None."""
    height, width = image.shape[:2]
    scale = min(1.0, DETECT_SIDE / max(height, width))
    small = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))),
                       interpolation=cv2.INTER_AREA) if scale < 1 else image
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    area_total = gray.shape[0] * gray.shape[1]
    smooth = cv2.GaussianBlur(gray, (5, 5), 0).astype(np.float32)
    magnitude = cv2.magnitude(cv2.Sobel(smooth, cv2.CV_32F, 1, 0), cv2.Sobel(smooth, cv2.CV_32F, 0, 1))
    # Thin outlines: let each side sample the strongest response within a few pixels.
    magnitude = cv2.dilate(magnitude, np.ones((5, 5), np.uint8))
    best = None
    found = [candidate for binary in _edge_maps(gray) for candidate in _candidates(binary, area_total, magnitude)]
    found.extend(_line_candidates(gray, area_total, magnitude))
    for candidate in found:
        if best is None or candidate[1] > best[1]:
            best = candidate
    if best is not None:
        # Prefer the outermost outline that is nearly as good, as well supported, and
        # encloses the best one: a strong inner edge (the card's border inside a slab) must
        # not cut the slab short, while a faint sleeve fold must not extend it.
        enclosing = [(cv2.contourArea(corners), corners) for corners, score, support in found
                     if score >= 0.8 * best[1] and support >= 0.7 * best[2]
                     and all(cv2.pointPolygonTest(corners.reshape(-1, 1, 2), (float(x), float(y)), True) >= -3
                             for x, y in best[0])]
        best = (max(enclosing, key=lambda item: item[0])[1],)
    if best is None:
        return None
    corners = best[0] / scale
    corners[:, 0] = corners[:, 0].clip(0, width - 1)
    corners[:, 1] = corners[:, 1].clip(0, height - 1)
    return [[round(float(x), 1), round(float(y), 1)] for x, y in corners]


def crop_card(image, corners):
    """Upright WebP of the area inside four corners (any order)."""
    source = order_corners(corners)
    width, height = _side_lengths(source)
    if width < 16 or height < 16:
        raise ImageError('graded_image_crop')
    scale = min(1.0, OUTPUT_SIDE / max(width, height))
    out_w, out_h = max(1, round(width * scale)), max(1, round(height * scale))
    target = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(source, target)
    warped = cv2.warpPerspective(image, matrix, (out_w, out_h), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)
    output = BytesIO()
    Image.fromarray(warped).save(output, format='WEBP', quality=85)
    return output.getvalue(), (out_w, out_h)
