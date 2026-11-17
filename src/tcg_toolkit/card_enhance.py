"""Optional brightness calibration for card photos (glare and uneven light).

Path: find specular highlights, remove the white veil they lay over the card (local
dark-channel prior), flatten the large-scale illumination, then add mild local contrast
(CLAHE). Works on the LAB lightness channel so hues are kept. Mild by design: it should
make a photographed slab readable, not restyle it.
"""
import cv2
import numpy as np


def _smooth(image, sigma):
    """Gaussian blur; wide ones run on a reduced copy (same result for smooth fields, far faster)."""
    if sigma <= 12:
        return cv2.GaussianBlur(image, (0, 0), sigma)
    factor = sigma / 6
    height, width = image.shape[:2]
    small = cv2.resize(image, (max(1, round(width / factor)), max(1, round(height / factor))), interpolation=cv2.INTER_AREA)
    return cv2.resize(cv2.GaussianBlur(small, (0, 0), 6), (width, height), interpolation=cv2.INTER_LINEAR)


def _fill(channel, weights, sigma):
    """Blur that ignores masked pixels (normalized convolution)."""
    blurred = _smooth(channel * weights, sigma)
    norm = _smooth(weights, sigma)
    return blurred / np.maximum(norm, 1e-3)


def glare_mask(rgb):
    """Soft 0-1 mask of specular highlights: bright, colourless and brighter than nearby."""
    height, width = rgb.shape[:2]
    scale = max(height, width)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    lightness = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[:, :, 0].astype(np.float32)
    # The surface level around each pixel, with thin dark print (text, lines) closed over so
    # white paper between letters is not "brighter than its surroundings". White paper then
    # matches its own level; glare is light added on top of the surface.
    size = max(3, int(scale / 90) | 1)
    surface = cv2.morphologyEx(lightness, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    # Reference white: the level of the brighter surfaces (card, slab plastic) at a large
    # scale, with the dark background left out. A white card matches it up to its edges;
    # a glare spot or veil, whatever its size, is brighter than the surface it lies on.
    glaring = np.clip((hsv[:, :, 2] - 165) / 40, 0, 1)
    colourless = np.clip((95 - hsv[:, :, 1]) / 60, 0, 1)
    # Diffuse white (paper, plastic) is the reference; clearly saturated colourless pixels
    # are left out of it, or a large bright veil would set its own bar.
    low, high = np.percentile(surface, [50, 97])
    saturated = np.clip((hsv[:, :, 2] - 232) / 15, 0, 1) * colourless
    bright = np.clip((surface - low) / max(float(high - low), 1.0), 0, 1) ** 3 * (1 - saturated)
    reference = _fill(surface, bright, scale / 8)
    # Two kinds of glare: a bright sheen above the diffuse white, and a veil over darker
    # artwork that is only brighter than the artwork around it.
    local = _smooth(surface, scale / 30)
    excess = np.maximum(np.clip((lightness - reference - 12) / 22, 0, 1),
                        np.clip((lightness - local - 10) / 25, 0, 1))
    mask = excess * glaring * colourless
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    return np.clip(_smooth(mask, scale / 300) * 1.4, 0, 1)


def remove_glare(rgb, mask, scale):
    """Undo a white veil inside the glare mask (local dark-channel prior).

    A veil adds white light: seen = picture * (1 - a) + 255 * a. Every neighbourhood of a
    card holds some dark ink in at least one colour channel, so how far its darkest channel
    is lifted estimates a; the picture underneath is then recovered, colour included. Kept
    at 85% so a trace of the shine stays and nothing is over-corrected.
    """
    image = rgb.astype(np.float32)
    size = max(3, int(scale / 60) | 1)
    darkest = cv2.erode(image.min(axis=2), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    darkest = _smooth(darkest, scale / 120)
    # Relative to the darker surfaces just outside the glare (ink, artwork: what the card
    # under the veil is likely made of), not to white paper; capped so noise is amplified
    # 2.5x at most and white paper under light glare is barely touched.
    outside = _fill(darkest, (1.0 - mask) * (1 - darkest / 255) ** 2, scale / 30)
    veil = np.clip((darkest - outside) / np.maximum(255 - outside, 1.0) * 0.85, 0, 0.6) * mask
    recovered = (image - 255 * veil[:, :, None]) / (1 - veil[:, :, None])
    return np.clip(recovered, 0, 255).astype(np.uint8)


def calibrate(rgb, strength=1.0):
    """Brightness-calibrated copy of an RGB uint8 image (same size)."""
    height, width = rgb.shape[:2]
    scale = max(height, width)
    mask = glare_mask(rgb) * strength

    # 1. Highlight suppression: remove the glare veil where it was detected.
    lab = cv2.cvtColor(remove_glare(rgb, mask, scale), cv2.COLOR_RGB2LAB).astype(np.float32)
    lightness = lab[:, :, 0]

    # 2. Local illumination normalization: estimate the light falling on each area as the
    #    local bright envelope (print closed over, glare left out) and lift areas that get
    #    less of it towards the well-lit level. Evenly lit areas keep their brightness;
    #    the gain is capped so dark backgrounds are not blown up.
    size = max(3, int(scale / 60) | 1)
    envelope = cv2.dilate(lightness, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    illumination = _fill(envelope, 1.0 - mask, scale / 12)
    target = float(np.percentile(illumination, 90))
    gain = np.clip(target / np.maximum(illumination, 1.0), 0.9, 1.25)
    lightness = lightness * (1 + (gain - 1) * 0.6 * strength)

    # 3. Mild contrast: CLAHE on lightness, blended in partly.
    clahe = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8))
    contrast = clahe.apply(np.clip(lightness, 0, 255).astype(np.uint8)).astype(np.float32)
    lab[:, :, 0] = lightness * (1 - 0.55 * strength) + contrast * 0.55 * strength
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)
