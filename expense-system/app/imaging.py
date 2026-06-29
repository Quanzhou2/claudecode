"""Perceptual image hashing for payment-voucher similarity detection.

Uses a 64-bit dHash (difference hash): downscale to grayscale 9x8 and record,
for each pixel, whether it is brighter than its right neighbour. Visually
similar images (re-saved, re-compressed, lightly cropped, different status-bar
time) produce hashes with a small Hamming distance, so similarity is robust
where an exact byte hash would miss.
"""
from __future__ import annotations

import io

try:  # Pillow is required for real hashing; degrade gracefully if absent.
    from PIL import Image

    _PIL_OK = True
except Exception:  # noqa: BLE001
    _PIL_OK = False

HASH_BITS = 64


def perceptual_hash(image_bytes: bytes) -> str | None:
    """Return a 16-hex-char (64-bit) dHash, or None if the image can't be read."""
    if not _PIL_OK:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize(
            (9, 8), Image.LANCZOS
        )
    except Exception:  # noqa: BLE001 — unreadable/corrupt image
        return None
    px = img.load()
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[col, row] > px[col + 1, row] else 0)
    return f"{bits:016x}"


def hamming_distance(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def similarity(a: str | None, b: str | None) -> float:
    """Fraction of matching bits in [0, 1]; 1.0 means identical hashes."""
    if not a or not b:
        return 0.0
    try:
        return 1.0 - hamming_distance(a, b) / HASH_BITS
    except ValueError:
        return 0.0
