"""dHash：只靠 Pillow，避免 numpy / OpenCV / scipy，打包比較瘦。"""

from __future__ import annotations

from PIL import Image


def dhash_int(image: Image.Image, hash_size: int = 8) -> int:
    gray = image.convert("L").resize(
        (hash_size + 1, hash_size), Image.Resampling.LANCZOS
    )
    pixels = list(gray.getdata())
    width = hash_size + 1
    value = 0
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * width + col]
            right = pixels[row * width + col + 1]
            value = (value << 1) | int(left < right)
    return value


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def min_distance(frame_hash: int, references: list[int]) -> int | None:
    if not references:
        return None
    return min(hamming_distance(frame_hash, ref) for ref in references)
