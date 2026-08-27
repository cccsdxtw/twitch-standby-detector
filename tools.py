"""尋找 FFmpeg / Streamlink 執行檔（EXE 旁優先，其次 PATH）。"""

from __future__ import annotations

import os
import shutil
import sys

from paths import app_dir


def find_executable(name: str) -> str | None:
    names = [name]
    if sys.platform == "win32" and not name.lower().endswith(".exe"):
        names.append(f"{name}.exe")

    bases = [app_dir()]
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:
        bases.append(meipass)

    for base in bases:
        for filename in names:
            candidate = os.path.join(base, filename)
            if os.path.isfile(candidate):
                return candidate

    for filename in names:
        found = shutil.which(filename)
        if found:
            return found
    return None


def find_ffmpeg() -> str | None:
    return find_executable("ffmpeg")


def find_streamlink_cli() -> str | None:
    return find_executable("streamlink")
