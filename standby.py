"""待命畫面判定：參考圖或開台後穩定 baseline，連續不像才算正片。"""

from __future__ import annotations

import os
import subprocess

from PIL import Image, UnidentifiedImageError

from image_hash import dhash_int, hamming_distance, min_distance
from tools import find_ffmpeg

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}


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


def clear_reference_files(standby_dir: str, login: str) -> None:
    os.makedirs(standby_dir, exist_ok=True)
    for path in list_reference_files(standby_dir, login):
        try:
            os.remove(path)
        except OSError:
            continue


def describe_references(standby_dir: str, login: str) -> str:
    files = list_reference_files(standby_dir, login)
    if not files:
        return "尚未指定待命畫面"
    if len(files) == 1:
        return os.path.basename(files[0])
    return f"{len(files)} 張待命樣本"


def import_standby_image(standby_dir: str, login: str, source: str) -> list[str]:
    login = login.lower()
    os.makedirs(standby_dir, exist_ok=True)
    clear_reference_files(standby_dir, login)
    dest = os.path.join(standby_dir, f"{login}.png")
    with Image.open(source) as img:
        img.load()
        img.convert("RGB").save(dest, "PNG")
    return [dest]


def import_standby_video(standby_dir: str, login: str, source: str) -> list[str]:
    login = login.lower()
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg，無法從影片抽幀")
    os.makedirs(standby_dir, exist_ok=True)
    clear_reference_files(standby_dir, login)
    pattern = os.path.join(standby_dir, f"{login}-%02d.png")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        source,
        "-vf",
        "fps=1,scale=640:-1",
        "-frames:v",
        "5",
        pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    files = list_reference_files(standby_dir, login)
    if result.returncode != 0 or not files:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or "從影片抽幀失敗")
    return files


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
