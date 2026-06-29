import io

from PIL import Image, ImageDraw

from app.imaging import hamming_distance, perceptual_hash, similarity


def _png(img) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def _structured(seed: int):
    img = Image.new("RGB", (200, 320), "white")
    d = ImageDraw.Draw(img)
    for i, y in enumerate(range(20, 300, 24)):
        d.rectangle([10, y, 10 + ((i * seed) % 160) + 20, y + 14], fill=(40 + i * 9 % 200,) * 3)
    return img


def test_similarity_bounds_and_distance():
    assert similarity("ffffffffffffffff", "ffffffffffffffff") == 1.0
    assert similarity("ffffffffffffffff", "0000000000000000") == 0.0
    assert hamming_distance("ffffffffffffffff", "fffffffffffffff0") == 4
    assert similarity("ffffffffffffffff", "fffffffffffffff0") == (64 - 4) / 64
    assert similarity(None, "ff") == 0.0


def test_perceptual_hash_identical_and_recompressed():
    img = _structured(7)
    h1 = perceptual_hash(_png(img))
    assert h1 and len(h1) == 16
    # Re-encoding as JPEG must stay highly similar.
    jb = io.BytesIO()
    img.save(jb, "JPEG", quality=70)
    assert similarity(h1, perceptual_hash(jb.getvalue())) >= 0.9


def test_perceptual_hash_distinguishes_different_images():
    h1 = perceptual_hash(_png(_structured(7)))
    h2 = perceptual_hash(_png(_structured(31)))
    assert similarity(h1, h2) < 0.8


def test_perceptual_hash_handles_bad_bytes():
    assert perceptual_hash(b"not an image") is None
