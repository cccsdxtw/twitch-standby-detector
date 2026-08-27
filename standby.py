"""待命畫面判定：參考圖或開台後穩定 baseline，連續不像才算正片。"""

from __future__ import annotations

import os

from PIL import Image, UnidentifiedImageError

from image_hash import dhash_int, hamming_distance, min_distance

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def list_reference_files(standby_dir: str, login: str) -> list[str]:
    if not os.path.isdir(standby_dir):
        return []
    login = login.lower()
    found: list[str] = []
    for name in sorted(os.listdir(standby_dir)):
        stem, ext = os.path.splitext(name)
        if ext.lower() not in IMAGE_EXTS:
            continue
        stem_l = stem.lower()
        if stem_l == login or stem_l.startswith(f"{login}-") or stem_l.startswith(f"{login}_"):
            found.append(os.path.join(standby_dir, name))
    return found


def load_reference_hashes(standby_dir: str, login: str) -> tuple[list[int], list[str]]:
    hashes: list[int] = []
    used: list[str] = []
    for path in list_reference_files(standby_dir, login):
        try:
            with Image.open(path) as img:
                img.load()
                hashes.append(dhash_int(img))
            used.append(path)
        except (OSError, UnidentifiedImageError):
            continue
    return hashes, used


def hashes_are_stable(window: list[int], threshold: int) -> bool:
    if len(window) < 3:
        return False
    sample = window[-3:]
    for i in range(len(sample)):
        for j in range(i + 1, len(sample)):
            if hamming_distance(sample[i], sample[j]) > threshold:
                return False
    return True


class StandbyDetector:
    def __init__(self, references: list[int], threshold: int, confirm_frames: int) -> None:
        self.references = list(references)
        self.threshold = threshold
        self.confirm_frames = max(int(confirm_frames), 1)
        self.unlike_streak = 0

    def set_references(self, references: list[int]) -> None:
        self.references = list(references)
        self.unlike_streak = 0

    def observe(self, frame_hash: int) -> tuple[str, int | None]:
        """回傳 (standby|pending|content, 與最近待命的距離)。"""
        dist = min_distance(frame_hash, self.references)
        if dist is None:
            return "pending", None
        if dist <= self.threshold:
            self.unlike_streak = 0
            return "standby", dist
        self.unlike_streak += 1
        if self.unlike_streak >= self.confirm_frames:
            return "content", dist
        return "pending", dist
