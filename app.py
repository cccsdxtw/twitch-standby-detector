"""實況守門員 — 單檔版，方便拖進萬用 PyInstaller 打包。"""

from __future__ import annotations

import asyncio
import io
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

import httpx
import tkinter as tk
from dotenv import load_dotenv
from PIL import Image, ImageDraw, UnidentifiedImageError
from tkinter import colorchooser, filedialog, scrolledtext


# === ui_theme.py ===

import tkinter as tk

BG = "#F3F3F3"
PANEL = "#F3F3F3"
LOG_BG = "#FFFFFF"
FG = "#1A1A1A"
MUTED = "#555555"
OK = "#1B8A3A"
WARN = "#B45309"
ERR = "#B42318"

ORANGE = "#E67A1A"
RED = "#C0392B"
BLUE = "#1E7FE0"
NAVY = "#2C3E50"
PURPLE = "#6C3BAA"
GRAY = "#4A4A4A"
GREEN = "#2E8B57"

FONT = ("Microsoft JhengHei UI", 10)
FONT_BOLD = ("Microsoft JhengHei UI", 11, "bold")
FONT_TITLE = ("Microsoft JhengHei UI", 12, "bold")
FONT_LOG = ("Microsoft JhengHei UI", 10)
FONT_SMALL = ("Microsoft JhengHei UI", 9)


def apply_root(root: tk.Tk) -> None:
    root.configure(bg=BG)
    try:
        root.option_add("*Font", FONT)
    except tk.TclError:
        pass


def group(parent: tk.Widget, title: str) -> tk.LabelFrame:
    box = tk.LabelFrame(
        parent,
        text=f" {title} ",
        font=FONT_BOLD,
        bg=PANEL,
        fg=FG,
        padx=10,
        pady=8,
        labelanchor="n",
    )
    return box


def label(parent: tk.Widget, text: str, *, bold: bool = False, fg: str = FG) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=getattr(parent, "cget", lambda _k: PANEL)("bg") if hasattr(parent, "cget") else PANEL,
        fg=fg,
        font=FONT_BOLD if bold else FONT,
        anchor="w",
    )


def entry(parent: tk.Widget, textvariable: tk.StringVar | None = None, **kwargs) -> tk.Entry:
    widget = tk.Entry(
        parent,
        textvariable=textvariable,
        font=FONT,
        relief=tk.SOLID,
        bd=1,
        **kwargs,
    )
    return widget


def color_button(
    parent: tk.Widget,
    text: str,
    command,
    bg: str,
    *,
    fg: str = "white",
    width: int | None = None,
) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=bg,
        activeforeground=fg,
        font=FONT_BOLD,
        relief=tk.FLAT,
        padx=12,
        pady=7,
        cursor="hand2",
        width=width,
    )


def small_button(parent: tk.Widget, text: str, command, bg: str) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg="white",
        activebackground=bg,
        activeforeground="white",
        font=("Microsoft JhengHei UI", 9, "bold"),
        relief=tk.FLAT,
        padx=8,
        pady=4,
        cursor="hand2",
    )


AVATAR_SIZE = 24
_HSCROLL_SKIP = (tk.Entry, tk.Spinbox, tk.Button, tk.Checkbutton)


def bind_hscroll_drag(canvas: tk.Canvas, widget: tk.Widget) -> None:
    """讓頻道列可用滑鼠左右拖動，略過輸入框與按鈕以免搶操作。"""

    def _start(event: tk.Event) -> None:
        canvas.scan_mark(event.x_root, 0)

    def _move(event: tk.Event) -> str | None:
        canvas.scan_dragto(event.x_root, 0, gain=1)
        return "break"

    def _walk(node: tk.Widget) -> None:
        if isinstance(node, _HSCROLL_SKIP):
            return
        node.bind("<ButtonPress-1>", _start, add="+")
        node.bind("<B1-Motion>", _move, add="+")
        for child in node.winfo_children():
            _walk(child)

    _walk(widget)


def attach_hscroll(parent: tk.Widget) -> tuple[tk.Canvas, tk.Frame]:
    """回傳 (canvas, inner)。inner 用來放頻道列，超出寬度可左右拖。"""
    wrap = tk.Frame(parent, bg=PANEL)
    wrap.pack(fill=tk.X)
    canvas = tk.Canvas(wrap, bg=PANEL, highlightthickness=0, bd=0, height=40)
    bar = tk.Scrollbar(wrap, orient=tk.HORIZONTAL, command=canvas.xview)
    canvas.configure(xscrollcommand=bar.set)
    canvas.pack(fill=tk.X)
    bar.pack(fill=tk.X)
    inner = tk.Frame(canvas, bg=PANEL)
    window = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _sync(_event: tk.Event | None = None) -> None:
        inner.update_idletasks()
        bbox = canvas.bbox("all")
        if not bbox:
            return
        canvas.configure(scrollregion=bbox)
        canvas.configure(height=max(40, bbox[3] - bbox[1]))
        canvas.itemconfigure(window, height=bbox[3] - bbox[1])

    inner.bind("<Configure>", _sync)
    bind_hscroll_drag(canvas, canvas)
    bind_hscroll_drag(canvas, inner)
    return canvas, inner


# === paths.py ===

import os
import sys


def app_dir() -> str:
    """使用者可編輯檔案（.env、token）所在目錄：開發=腳本目錄，打包=EXE 目錄。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_path(relative_path: str) -> str:
    """打包神器會把圖示以 app_master_icon.ico 塞進程式內部，執行時從這裡讀。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


def avatar_cache_dir() -> str:
    path = os.path.join(app_dir(), "avatar_cache")
    os.makedirs(path, exist_ok=True)
    return path


def avatar_cache_path(login: str) -> str:
    return os.path.join(avatar_cache_dir(), f"{normalize_login(login)}.png")


def prepare_avatar_image(image: Image.Image, size: int = AVATAR_SIZE) -> Image.Image:
    """縮成圓形頭像，給 Tk 顯示。"""
    square = image.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    square.putalpha(mask)
    return square


def write_avatar_bytes(data: bytes, dest: str, size: int = AVATAR_SIZE) -> str:
    with Image.open(io.BytesIO(data)) as src:
        prepare_avatar_image(src, size).save(dest, "PNG")
    return dest


async def cache_profile_image(
    client: httpx.AsyncClient, url: str, dest: str
) -> bool:
    if not url:
        return False
    try:
        response = await client.get(url, timeout=20.0, follow_redirects=True)
        if response.status_code >= 400 or not response.content:
            return False
        write_avatar_bytes(response.content, dest)
        return True
    except (httpx.HTTPError, OSError, UnidentifiedImageError, ValueError):
        return False


# === image_hash.py ===

from PIL import Image


DHASH_SIZE = 8
DHASH_BITS = DHASH_SIZE * DHASH_SIZE
DEFAULT_SIMILARITY_PCT = 60
DEFAULT_IGNORE_TOLERANCE = 40


def clamp_similarity_pct(value: object, default: int = DEFAULT_SIMILARITY_PCT) -> int:
    try:
        pct = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        pct = default
    return max(1, min(99, pct))


def clamp_ignore_tolerance(value: object, default: int = DEFAULT_IGNORE_TOLERANCE) -> int:
    try:
        tol = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        tol = default
    return max(0, min(120, tol))


def similarity_pct_to_threshold(pct: int, bits: int = DHASH_BITS) -> int:
    """相似度 >= pct 視為像待命。60% → 最多可差約 25/64。"""
    pct = clamp_similarity_pct(pct)
    return max(0, bits - math.ceil(bits * pct / 100))


def hash_similarity_pct(dist: int, bits: int = DHASH_BITS) -> int:
    return max(0, min(100, int(round((bits - dist) * 100 / bits))))


def parse_ignore_color(text: str) -> tuple[int, int, int] | None:
    raw = (text or "").strip().lstrip("#")
    if len(raw) != 6:
        return None
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except ValueError:
        return None


def format_ignore_color(color: tuple[int, int, int]) -> str:
    return f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"


def apply_ignore_color(
    image: Image.Image,
    color: tuple[int, int, int] | None,
    tolerance: int = DEFAULT_IGNORE_TOLERANCE,
) -> Image.Image:
    """略過色的像素改填未略過區域的平均色，標題改字就比較不會動到 hash。"""
    rgb = image.convert("RGB")
    if color is None:
        return rgb
    pixels = list(rgb.getdata())
    cr, cg, cb = color
    tol = clamp_ignore_tolerance(tolerance)
    mask: list[bool] = []
    kept: list[tuple[int, int, int]] = []
    for r, g, b in pixels:
        skip = max(abs(r - cr), abs(g - cg), abs(b - cb)) <= tol
        mask.append(skip)
        if not skip:
            kept.append((r, g, b))
    if not kept or all(mask):
        return rgb
    fill = (
        sum(p[0] for p in kept) // len(kept),
        sum(p[1] for p in kept) // len(kept),
        sum(p[2] for p in kept) // len(kept),
    )
    rgb.putdata([fill if skip else px for skip, px in zip(mask, pixels)])
    return rgb


def dhash_int(
    image: Image.Image,
    hash_size: int = DHASH_SIZE,
    ignore_color: tuple[int, int, int] | None = None,
    ignore_tolerance: int = DEFAULT_IGNORE_TOLERANCE,
) -> int:
    prepared = apply_ignore_color(image, ignore_color, ignore_tolerance)
    gray = prepared.convert("L").resize(
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


# === tools.py ===

import os
import shutil
import sys



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


# === config.py ===

import json
import os
import re
from dataclasses import dataclass, replace

from dotenv import load_dotenv


DEFAULT_SIMULATE_LOGIN = "lanmeinotbeer"


@dataclass(frozen=True)
class Settings:
    twitch_client_id: str
    twitch_client_secret: str
    user_logins: tuple[str, ...]
    discord_webhook_url: str
    simulate: bool
    standby_dir: str
    frame_interval_sec: float
    ad_skip_sec: float
    confirm_frames: int
    hash_threshold: int
    skip_start_after_min: int

    @property
    def ready_for_eventsub(self) -> bool:
        return bool(self.twitch_client_id) and not self.simulate

    def with_logins(self, logins: tuple[str, ...]) -> Settings:
        return replace(self, user_logins=logins)


@dataclass(frozen=True)
class ChannelPref:
    login: str
    notify_live: bool = True
    notify_start: bool = True
    open_watch: bool = False
    close_watch: bool = False
    display_name: str = ""
    similarity_pct: int = DEFAULT_SIMILARITY_PCT
    ignore_color: str = ""
    ignore_tolerance: int = DEFAULT_IGNORE_TOLERANCE


def watchlist_path() -> str:
    return os.path.join(app_dir(), "watchlist.txt")


def watchlist_json_path() -> str:
    return os.path.join(app_dir(), "watchlist.json")


def normalize_login(item: str) -> str:
    login = item.strip().lower().rstrip("/")
    if "twitch.tv/" in login:
        login = login.split("twitch.tv/", 1)[1]
        login = login.split("?", 1)[0].split("/", 1)[0]
    return login


def parse_logins(raw: str) -> tuple[str, ...]:
    parts = []
    seen = set()
    for item in re.split(r"[,;\s]+", raw):
        login = normalize_login(item)
        if login and login not in seen:
            seen.add(login)
            parts.append(login)
    return tuple(parts)


def load_watchlist_text() -> str:
    path = watchlist_path()
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def save_watchlist(logins: tuple[str, ...]) -> None:
    prefs = [ChannelPref(login=login) for login in logins]
    save_channel_prefs(prefs)


def load_channel_prefs() -> list[ChannelPref]:
    json_path = watchlist_json_path()
    if os.path.isfile(json_path):
        try:
            with open(json_path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            raw = []
        prefs: list[ChannelPref] = []
        seen: set[str] = set()
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                login = normalize_login(str(item.get("login") or ""))
                if not login or login in seen:
                    continue
                seen.add(login)
                prefs.append(
                    ChannelPref(
                        login=login,
                        notify_live=bool(item.get("notify_live", True)),
                        notify_start=bool(item.get("notify_start", True)),
                        open_watch=bool(item.get("open_watch", False)),
                        close_watch=bool(item.get("close_watch", False)),
                        display_name=str(item.get("display_name") or ""),
                        similarity_pct=clamp_similarity_pct(
                            item.get("similarity_pct", DEFAULT_SIMILARITY_PCT)
                        ),
                        ignore_color=str(item.get("ignore_color") or ""),
                        ignore_tolerance=clamp_ignore_tolerance(
                            item.get("ignore_tolerance", DEFAULT_IGNORE_TOLERANCE)
                        ),
                    )
                )
        if prefs:
            return prefs

    logins = parse_logins(load_watchlist_text())
    return [ChannelPref(login=login) for login in logins]


def save_channel_prefs(prefs: list[ChannelPref]) -> None:
    payload = [
        {
            "login": pref.login,
            "notify_live": pref.notify_live,
            "notify_start": pref.notify_start,
            "open_watch": pref.open_watch,
            "close_watch": pref.close_watch,
            "display_name": pref.display_name,
            "similarity_pct": clamp_similarity_pct(pref.similarity_pct),
            "ignore_color": pref.ignore_color,
            "ignore_tolerance": clamp_ignore_tolerance(pref.ignore_tolerance),
        }
        for pref in prefs
        if pref.login
    ]
    with open(watchlist_json_path(), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with open(watchlist_path(), "w", encoding="utf-8") as handle:
        handle.write("\n".join(item["login"] for item in payload))
        if payload:
            handle.write("\n")


def env_path() -> str:
    return os.path.join(app_dir(), ".env")


def upsert_env_values(values: dict[str, str]) -> None:
    """更新 .env 指定鍵，其餘註解與項目原樣保留。"""
    path = env_path()
    lines: list[str] = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()

    seen: set[str] = set()
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in values:
            updated.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            updated.append(line)
    for key, value in values.items():
        if key not in seen:
            updated.append(f"{key}={value}")

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(updated))
        handle.write("\n")
    load_dotenv(path, override=True)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


DEFAULT_SKIP_START_AFTER_MIN = 60


def clamp_skip_start_after_min(
    value: object, default: int = DEFAULT_SKIP_START_AFTER_MIN
) -> int:
    try:
        minutes = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if minutes < 0:
        return default
    return min(minutes, 24 * 60)


def skip_start_hms(minutes: int) -> tuple[int, int]:
    minutes = clamp_skip_start_after_min(minutes)
    return minutes // 60, minutes % 60


def skip_start_after_label(minutes: int) -> str:
    minutes = clamp_skip_start_after_min(minutes)
    if minutes == 0:
        return "不略過"
    hours, mins = skip_start_hms(minutes)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} 小時")
    if mins:
        parts.append(f"{mins} 分鐘")
    return " ".join(parts)


def parse_helix_time(raw: object) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stream_live_for_seconds(
    started_at: datetime | None, *, now: datetime | None = None
) -> float | None:
    if started_at is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current - started_at.astimezone(timezone.utc)).total_seconds())


def should_skip_start_detect(
    started_at: datetime | None,
    skip_after_min: int,
    *,
    now: datetime | None = None,
) -> bool:
    limit = clamp_skip_start_after_min(skip_after_min)
    if limit <= 0:
        return False
    age = stream_live_for_seconds(started_at, now=now)
    if age is None:
        return False
    return age >= limit * 60


def load_settings() -> Settings:
    load_dotenv(env_path(), override=True)

    simulate = _truthy(os.getenv("SIMULATE", "0"))
    prefs = load_channel_prefs()
    logins = tuple(pref.login for pref in prefs) or parse_logins(
        os.getenv("TWITCH_USER_LOGINS", "")
    )
    if simulate and not logins:
        logins = (DEFAULT_SIMULATE_LOGIN,)

    return Settings(
        twitch_client_id=os.getenv("TWITCH_CLIENT_ID", "").strip(),
        twitch_client_secret=os.getenv("TWITCH_CLIENT_SECRET", "").strip(),
        user_logins=logins,
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
        simulate=simulate,
        standby_dir=os.path.join(app_dir(), "standby"),
        frame_interval_sec=max(_env_float("FRAME_INTERVAL_SEC", 3.0), 1.0),
        ad_skip_sec=max(_env_float("AD_SKIP_SEC", 20.0), 0.0),
        confirm_frames=max(_env_int("CONFIRM_FRAMES", 4), 1),
        hash_threshold=max(_env_int("HASH_THRESHOLD", 16), 1),
        skip_start_after_min=clamp_skip_start_after_min(
            os.getenv("SKIP_START_AFTER_MIN", str(DEFAULT_SKIP_START_AFTER_MIN))
        ),
    )


# === discord_notify.py ===

from typing import Callable

import httpx

LogFn = Callable[[str], None]


def build_webhook_body(content: str) -> dict:
    return {"content": content[:2000]}


def twitch_channel_url(login: str) -> str:
    handle = normalize_login(login)
    return f"https://www.twitch.tv/{handle}" if handle else ""


WATCH_CDP_PORT = 9333


def watch_profile_dir() -> str:
    path = os.path.join(app_dir(), "twitch_watch_profile")
    os.makedirs(path, exist_ok=True)
    return path


def find_browser_exe() -> str | None:
    override = (os.environ.get("TWITCH_BROWSER") or "").strip()
    if override and os.path.isfile(override):
        return override
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        for name in (
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "microsoft-edge",
        ):
            found = shutil.which(name)
            if found:
                return found
        candidates = []
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def cdp_new_tab_url(page_url: str, port: int = WATCH_CDP_PORT) -> str:
    return f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(page_url, safe='')}"


def cdp_close_tab_url(target_id: str, port: int = WATCH_CDP_PORT) -> str:
    return f"http://127.0.0.1:{port}/json/close/{urllib.parse.quote(target_id, safe='')}"


def parse_cdp_target_id(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("id") or "")
    return ""


class WatchBrowser:
    """獨立 Chromium/Edge 視窗：開台開頁、關台關頁。系統預設瀏覽器關不掉分頁。"""

    def __init__(self, port: int = WATCH_CDP_PORT) -> None:
        self.port = port
        self._proc: subprocess.Popen[bytes] | None = None
        self._targets: dict[str, str] = {}
        self.used_system_fallback: set[str] = set()

    def open_channel(self, login: str) -> bool:
        handle = normalize_login(login)
        url = twitch_channel_url(handle)
        if not url:
            return False
        if handle in self._targets:
            return True
        if self._ensure_browser():
            try:
                target = self._cdp_open(url)
            except (OSError, TimeoutError, json.JSONDecodeError, ValueError):
                target = ""
            if target:
                self._targets[handle] = target
                self.used_system_fallback.discard(handle)
                return True
        try:
            ok = bool(webbrowser.open(url, new=2))
        except Exception:
            return False
        if ok:
            self.used_system_fallback.add(handle)
        return ok

    def has_page(self, login: str) -> bool:
        handle = normalize_login(login)
        return handle in self._targets or handle in self.used_system_fallback

    def close_channel(self, login: str) -> bool:
        handle = normalize_login(login)
        target = self._targets.pop(handle, "")
        self.used_system_fallback.discard(handle)
        if not target:
            return False
        try:
            self._cdp_close(target)
            return True
        except (OSError, TimeoutError, ValueError):
            return False

    def close_all(self) -> None:
        for login in list(self._targets) + list(self.used_system_fallback):
            self.close_channel(login)

    def _cdp_ready(self) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/json/version", timeout=0.4
            ) as resp:
                return 200 <= getattr(resp, "status", 200) < 300
        except (OSError, TimeoutError):
            return False

    def _ensure_browser(self) -> bool:
        if self._cdp_ready():
            return True
        exe = find_browser_exe()
        if not exe:
            return False
        args = [
            exe,
            f"--user-data-dir={watch_profile_dir()}",
            f"--remote-debugging-port={self.port}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        try:
            self._proc = subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError:
            return False
        for _ in range(50):
            if self._cdp_ready():
                return True
            time.sleep(0.1)
        return False

    def _cdp_open(self, url: str) -> str:
        with urllib.request.urlopen(cdp_new_tab_url(url, self.port), timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
        return parse_cdp_target_id(payload)

    def _cdp_close(self, target_id: str) -> None:
        with urllib.request.urlopen(cdp_close_tab_url(target_id, self.port), timeout=8):
            pass


def build_live_message(display_name: str, login: str) -> str:
    name = (display_name or login).strip() or login
    handle = (login or "").strip().lower()
    return f"「{name}」在實況了\n來去 {twitch_channel_url(handle)} 看看"


def build_start_message(display_name: str, login: str) -> str:
    name = (display_name or login).strip() or login
    handle = (login or "").strip().lower()
    return f"「{name}」正片開始了\n來去 https://www.twitch.tv/{handle} 看看"


async def send_webhook(
    webhook_url: str,
    content: str,
    *,
    client: httpx.AsyncClient,
    log: LogFn,
) -> None:
    if not webhook_url:
        log("ℹ️ 未設定 DISCORD_WEBHOOK_URL，略過 Discord 通知")
        return
    try:
        response = await client.post(
            webhook_url,
            json=build_webhook_body(content),
            timeout=30.0,
        )
        if response.status_code >= 400:
            log(f"❌ Discord 通知失敗 HTTP {response.status_code}: {response.text[:300]}")
            return
        log("✅ 已發送 Discord 通知")
    except httpx.HTTPError as exc:
        log(f"❌ Discord 通知網路錯誤：{exc}")


# === standby.py ===

import os
import subprocess

from PIL import Image, UnidentifiedImageError


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


def load_reference_hashes(
    standby_dir: str,
    login: str,
    ignore_color: tuple[int, int, int] | None = None,
    ignore_tolerance: int = DEFAULT_IGNORE_TOLERANCE,
) -> tuple[list[int], list[str]]:
    hashes: list[int] = []
    used: list[str] = []
    for path in list_reference_files(standby_dir, login):
        try:
            with Image.open(path) as img:
                img.load()
                hashes.append(
                    dhash_int(
                        img,
                        ignore_color=ignore_color,
                        ignore_tolerance=ignore_tolerance,
                    )
                )
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


# === twitch_oauth.py ===

import asyncio
import json
import os
import time
import webbrowser
from dataclasses import dataclass
from typing import Callable

import httpx


TOKEN_FILENAME = "twitch_token.json"
DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# stream.online 不需要額外 scope；空字串代表「無 scope 的使用者 token」。
DEVICE_SCOPES = ""

LogFn = Callable[[str], None]


class TwitchAuthError(RuntimeError):
    pass


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_at: float

    def access_valid(self, skew_seconds: float = 60) -> bool:
        return bool(self.access_token) and time.time() < (self.expires_at - skew_seconds)


def token_path() -> str:
    return os.path.join(app_dir(), TOKEN_FILENAME)


def load_token() -> TokenPair | None:
    path = token_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return TokenPair(
            access_token=str(data.get("access_token", "")),
            refresh_token=str(data.get("refresh_token", "")),
            expires_at=float(data.get("expires_at", 0)),
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def save_token(pair: TokenPair) -> None:
    path = token_path()
    payload = {
        "access_token": pair.access_token,
        "refresh_token": pair.refresh_token,
        "expires_at": pair.expires_at,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def token_from_response(data: dict) -> TokenPair:
    expires_in = int(data.get("expires_in") or 0)
    return TokenPair(
        access_token=str(data.get("access_token", "")),
        refresh_token=str(data.get("refresh_token", "")),
        expires_at=time.time() + max(expires_in, 0),
    )


async def refresh_token(
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    refresh: str,
) -> TokenPair:
    form = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    }
    if client_secret:
        form["client_secret"] = client_secret
    response = await client.post(TOKEN_URL, data=form, timeout=30.0)
    data = response.json() if response.content else {}
    if response.status_code >= 400 or not data.get("access_token"):
        raise TwitchAuthError(f"刷新 token 失敗 HTTP {response.status_code}: {response.text[:400]}")
    pair = token_from_response(data)
    if not pair.refresh_token:
        pair.refresh_token = refresh
    save_token(pair)
    return pair


async def start_device_flow(
    client: httpx.AsyncClient,
    client_id: str,
) -> dict:
    form = {"client_id": client_id}
    if DEVICE_SCOPES:
        form["scopes"] = DEVICE_SCOPES
    response = await client.post(DEVICE_URL, data=form, timeout=30.0)
    if response.status_code >= 400:
        raise TwitchAuthError(f"啟動 Device Code 失敗 HTTP {response.status_code}: {response.text[:400]}")
    return response.json()


async def poll_device_token(
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    device_code: str,
    interval: int,
    expires_in: int,
    stop_event: asyncio.Event,
    log: LogFn,
) -> TokenPair:
    deadline = time.time() + max(expires_in, 30)
    wait = max(int(interval), 1)
    form = {
        "client_id": client_id,
        "device_code": device_code,
        "grant_type": DEVICE_GRANT,
    }
    if DEVICE_SCOPES:
        form["scopes"] = DEVICE_SCOPES
    if client_secret:
        form["client_secret"] = client_secret

    while time.time() < deadline:
        if stop_event.is_set():
            raise TwitchAuthError("登入已取消")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait)
            raise TwitchAuthError("登入已取消")
        except asyncio.TimeoutError:
            pass

        response = await client.post(TOKEN_URL, data=form, timeout=30.0)
        data = response.json() if response.content else {}
        if response.status_code < 400 and data.get("access_token"):
            pair = token_from_response(data)
            save_token(pair)
            return pair

        message = str(data.get("message") or data.get("status") or "").lower()
        if "pending" in message or response.status_code == 400 and "authorization_pending" in str(data):
            continue
        if "slow" in message:
            wait += 5
            log(f"⏳ Twitch 要求放慢輪詢，改為每 {wait} 秒一次")
            continue
        raise TwitchAuthError(f"Device Code 換 token 失敗 HTTP {response.status_code}: {response.text[:400]}")

    raise TwitchAuthError("Device Code 已過期，請再按啟動重試")


async def ensure_user_token(
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    stop_event: asyncio.Event,
    log: LogFn,
) -> TokenPair:
    stored = load_token()
    if stored and stored.access_valid():
        log("🔑 使用本機已儲存的 Twitch 使用者 token")
        return stored
    if stored and stored.refresh_token:
        log("🔑 使用者 token 已過期，正在刷新…")
        try:
            return await refresh_token(client, client_id, client_secret, stored.refresh_token)
        except TwitchAuthError as exc:
            log(f"⚠️ 刷新失敗，改走瀏覽器登入：{exc}")

    log("🔐 EventSub WebSocket 需要使用者登入（無額外權限）。")
    device = await start_device_flow(client, client_id)
    user_code = device.get("user_code", "")
    uri = device.get("verification_uri", "https://www.twitch.tv/activate")
    log(f"👉 請打開：{uri}")
    log(f"👉 輸入代碼：{user_code}")
    try:
        webbrowser.open(uri)
    except Exception:
        pass
    return await poll_device_token(
        client,
        client_id,
        client_secret,
        str(device.get("device_code", "")),
        int(device.get("interval") or 5),
        int(device.get("expires_in") or 1800),
        stop_event,
        log,
    )


# === twitch_helix.py ===

from dataclasses import dataclass
from typing import Any

import httpx

HELIX = "https://api.twitch.tv/helix"


class TwitchAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class TwitchUser:
    login: str
    user_id: str
    display_name: str
    profile_image_url: str = ""


def twitch_user_from_helix(user: dict[str, Any]) -> TwitchUser | None:
    login = str(user.get("login", "")).lower()
    uid = str(user.get("id", ""))
    display = str(user.get("display_name") or user.get("login") or "")
    avatar = str(user.get("profile_image_url") or "")
    if not login or not uid:
        return None
    return TwitchUser(
        login=login,
        user_id=uid,
        display_name=display,
        profile_image_url=avatar,
    )


def _headers(client_id: str, access_token: str) -> dict[str, str]:
    return {
        "Client-Id": client_id,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


async def _json(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        raise TwitchAPIError(f"Helix HTTP {response.status_code}: {response.text[:500]}")
    if not response.content:
        return {}
    return response.json()


async def resolve_users(
    client: httpx.AsyncClient,
    client_id: str,
    access_token: str,
    logins: tuple[str, ...],
) -> dict[str, TwitchUser]:
    """login -> TwitchUser。找不到的 login 不會出現在結果裡。"""
    if not logins:
        return {}
    params = [("login", login) for login in logins]
    response = await client.get(
        f"{HELIX}/users",
        headers=_headers(client_id, access_token),
        params=params,
        timeout=30.0,
    )
    payload = await _json(response)
    mapping: dict[str, TwitchUser] = {}
    for user in payload.get("data") or []:
        parsed = twitch_user_from_helix(user) if isinstance(user, dict) else None
        if parsed:
            mapping[parsed.login] = parsed
    return mapping


async def helix_token_for_lookup(
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
) -> str:
    """查顯示名稱用：優先本機使用者 token，沒有再試 App token。失敗回空字串。"""
    if not client_id:
        return ""
    stored = load_token()
    if stored and stored.access_valid():
        return stored.access_token
    if stored and stored.refresh_token:
        try:
            pair = await refresh_token(client, client_id, client_secret, stored.refresh_token)
            return pair.access_token
        except TwitchAuthError:
            pass
    if not client_secret:
        return ""
    response = await client.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=30.0,
    )
    data = response.json() if response.content else {}
    token = str(data.get("access_token") or "")
    return token if response.status_code < 400 and token else ""


async def live_streams(
    client: httpx.AsyncClient,
    client_id: str,
    access_token: str,
    user_ids: list[str],
) -> dict[str, datetime | None]:
    """user_id -> started_at（Helix）。"""
    if not user_ids:
        return {}
    live: dict[str, datetime | None] = {}
    for index in range(0, len(user_ids), 100):
        chunk = user_ids[index : index + 100]
        params = [("user_id", uid) for uid in chunk]
        response = await client.get(
            f"{HELIX}/streams",
            headers=_headers(client_id, access_token),
            params=params,
            timeout=30.0,
        )
        payload = await _json(response)
        for row in payload.get("data") or []:
            uid = str(row.get("user_id", ""))
            if uid:
                live[uid] = parse_helix_time(row.get("started_at"))
    return live


async def live_user_ids(
    client: httpx.AsyncClient,
    client_id: str,
    access_token: str,
    user_ids: list[str],
) -> set[str]:
    return set(
        (
            await live_streams(client, client_id, access_token, user_ids)
        ).keys()
    )


async def create_eventsub_subscription(
    client: httpx.AsyncClient,
    client_id: str,
    access_token: str,
    event_type: str,
    broadcaster_user_id: str,
    session_id: str,
) -> dict:
    body = {
        "type": event_type,
        "version": "1",
        "condition": {"broadcaster_user_id": broadcaster_user_id},
        "transport": {"method": "websocket", "session_id": session_id},
    }
    response = await client.post(
        f"{HELIX}/eventsub/subscriptions",
        headers=_headers(client_id, access_token),
        json=body,
        timeout=30.0,
    )
    return await _json(response)


# === twitch_stream.py ===

import re
from dataclasses import dataclass
from typing import Any

SKIP_KEYS = {"best", "worst", "audio_only"}
HEIGHT_RE = re.compile(r"(\d+)p", re.IGNORECASE)


class StreamResolveError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedStream:
    quality: str
    url: str | None


def parse_height(quality: str) -> int | None:
    match = HEIGHT_RE.search(quality)
    if not match:
        return None
    return int(match.group(1))


def pick_stream(streams: dict[str, Any]) -> tuple[str, Any]:
    ranked: list[tuple[int, str, Any]] = []
    for key, stream in streams.items():
        if key.lower() in SKIP_KEYS or "audio" in key.lower():
            continue
        height = parse_height(key)
        ranked.append((height if height is not None else 9999, key, stream))
    ranked.sort(key=lambda item: item[0])
    under = [item for item in ranked if item[0] <= 480]
    if under:
        chosen = under[-1]
        return chosen[1], chosen[2]
    if ranked:
        chosen = ranked[0]
        return chosen[1], chosen[2]
    for key in ("worst", "best"):
        if key in streams:
            return key, streams[key]
    raise StreamResolveError("沒有可用的影像畫質")


def resolve_twitch_stream(login: str) -> ResolvedStream:
    from streamlink import Streamlink

    session = Streamlink()
    session.set_option("hls-live-edge", 2)
    url = f"https://www.twitch.tv/{login}"
    streams = None
    last_error: Exception | None = None
    try:
        _, plugin_class, resolved_url = session.resolve_url(url)
        try:
            plugin = plugin_class(session, resolved_url, options={"disable-ads": True})
        except TypeError:
            plugin = plugin_class(session, resolved_url)
            setter = getattr(getattr(plugin, "options", None), "set", None)
            if callable(setter):
                setter("disable-ads", True)
        streams = plugin.streams()
    except Exception as exc:
        last_error = exc
        try:
            streams = session.streams(url)
        except Exception as exc2:
            raise StreamResolveError(str(exc2)) from exc2

    if not streams:
        raise StreamResolveError(str(last_error) if last_error else "找不到直播流（可能尚未真正開播）")

    quality, stream = pick_stream(streams)
    stream_url = getattr(stream, "url", None)
    if stream_url:
        stream_url = str(stream_url)
    return ResolvedStream(quality=quality, url=stream_url)


# === twitch_eventsub.py ===

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import websockets


WS_URL = "wss://eventsub.wss.twitch.tv/ws"
# 每種事件成本 1。只聽開台＝1，再勾關網頁才加 offline。
EVENT_ONLINE = "stream.online"
EVENT_OFFLINE = "stream.offline"
MAX_EVENTSUB_COST = 10


@dataclass(frozen=True)
class EventSubPlan:
    included: tuple[str, ...]
    skipped: tuple[str, ...]
    cost: int
    budget: int = MAX_EVENTSUB_COST

    @property
    def max_online_only(self) -> int:
        return self.budget

    @property
    def max_with_close(self) -> int:
        return self.budget // 2


def eventsub_types(pref: ChannelPref) -> tuple[str, ...]:
    types = [EVENT_ONLINE]
    if pref.close_watch:
        types.append(EVENT_OFFLINE)
    return tuple(types)


def eventsub_cost(pref: ChannelPref) -> int:
    return len(eventsub_types(pref)) if pref.login else 0


def plan_eventsub(
    prefs: list[ChannelPref], budget: int = MAX_EVENTSUB_COST
) -> EventSubPlan:
    included: list[str] = []
    skipped: list[str] = []
    cost = 0
    seen: set[str] = set()
    for pref in prefs:
        login = pref.login
        if not login or login in seen:
            continue
        seen.add(login)
        need = eventsub_cost(pref)
        if need and cost + need <= budget:
            included.append(login)
            cost += need
        else:
            skipped.append(login)
    return EventSubPlan(included=tuple(included), skipped=tuple(skipped), cost=cost, budget=budget)


def describe_eventsub_plan(plan: EventSubPlan) -> str:
    skip = f"，後面 {len(plan.skipped)} 台這次聽不到" if plan.skipped else ""
    return (
        f"EventSub 預算 {plan.cost}/{plan.budget}，這次可聽 {len(plan.included)} 台"
        f"（只聽開台最多 {plan.max_online_only}；有勾關網頁各多佔 1，全勾關最多 {plan.max_with_close}）"
        f"{skip}"
    )

LogFn = Callable[[str], None]
EventFn = Callable[[str, dict], Awaitable[None] | None]


async def _recv_or_stop(ws: Any, stop_event: asyncio.Event, timeout: float) -> str | bytes | None:
    """收到一則訊息；若使用者停止則回傳 None。逾時視為 keepalive 失敗。"""
    recv_task = asyncio.create_task(ws.recv())
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, pending = await asyncio.wait(
            {recv_task, stop_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if not done:
            raise RuntimeError("EventSub keepalive 逾時")
        if stop_task in done:
            return None
        return recv_task.result()
    finally:
        for task in (recv_task, stop_task):
            if not task.done():
                task.cancel()


class EventSubReconnect(Exception):
    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.url = url


def parse_ws_message(raw: str | bytes) -> tuple[str, dict[str, Any]]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    metadata = data.get("metadata") or {}
    return str(metadata.get("message_type") or ""), data


def event_from_notification(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    payload = data.get("payload") or {}
    subscription = payload.get("subscription") or {}
    event = payload.get("event") or {}
    return str(subscription.get("type") or ""), event


class EventSubClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        client_id: str,
        access_token: str,
        user_ids: dict[str, str],
        log: LogFn,
        on_event: EventFn,
        events_by_login: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.http = http
        self.client_id = client_id
        self.access_token = access_token
        self.user_ids = user_ids
        self.events_by_login = events_by_login or {
            login: (EVENT_ONLINE,) for login in user_ids
        }
        self.log = log
        self.on_event = on_event

    async def run(self, stop_event: asyncio.Event) -> None:
        url = WS_URL
        resubscribe = True
        backoff = 2.0
        while not stop_event.is_set():
            try:
                await self._run_session(url, resubscribe=resubscribe, stop_event=stop_event)
                url = WS_URL
                resubscribe = True
                backoff = 2.0
            except EventSubReconnect as exc:
                self.log("🔄 收到 session_reconnect，改接新的 WebSocket…")
                url = exc.url
                resubscribe = False
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if stop_event.is_set():
                    return
                self.log(f"⚠️ EventSub 連線中斷：{exc}，{backoff:.0f} 秒後重試")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                    return
                except asyncio.TimeoutError:
                    pass
                url = WS_URL
                resubscribe = True
                backoff = min(backoff * 2, 60)

    async def _run_session(
        self,
        url: str,
        *,
        resubscribe: bool,
        stop_event: asyncio.Event,
    ) -> None:
        self.log(f"🔌 連線 EventSub：{url}")
        async with websockets.connect(url, ping_interval=None, close_timeout=5) as ws:
            welcome_raw = await _recv_or_stop(ws, stop_event, 15)
            if welcome_raw is None:
                return
            msg_type, welcome = parse_ws_message(welcome_raw)
            if msg_type != "session_welcome":
                raise RuntimeError(f"預期 session_welcome，收到 {msg_type}")
            session = (welcome.get("payload") or {}).get("session") or {}
            session_id = str(session.get("id") or "")
            keepalive = int(session.get("keepalive_timeout_seconds") or 10)
            self.log(f"✅ EventSub session 已建立（keepalive {keepalive}s）")
            if resubscribe:
                await self._subscribe_all(session_id)
            recv_timeout = keepalive + 5
            while not stop_event.is_set():
                raw = await _recv_or_stop(ws, stop_event, recv_timeout)
                if raw is None:
                    return
                await self._dispatch(raw)

    async def _subscribe_all(self, session_id: str) -> None:
        for login, user_id in self.user_ids.items():
            for event_type in self.events_by_login.get(login, (EVENT_ONLINE,)):
                try:
                    await create_eventsub_subscription(
                        self.http,
                        self.client_id,
                        self.access_token,
                        event_type,
                        user_id,
                        session_id,
                    )
                    self.log(f"📡 已訂閱 {event_type} → {login} ({user_id})")
                except Exception as exc:
                    self.log(f"❌ 訂閱 {event_type} / {login} 失敗：{exc}")

    async def _dispatch(self, raw: str | bytes) -> None:
        msg_type, data = parse_ws_message(raw)
        if msg_type == "session_keepalive":
            return
        if msg_type == "session_reconnect":
            session = (data.get("payload") or {}).get("session") or {}
            reconnect_url = str(session.get("reconnect_url") or "")
            if reconnect_url:
                raise EventSubReconnect(reconnect_url)
            return
        if msg_type == "revocation":
            sub = (data.get("payload") or {}).get("subscription") or {}
            self.log(f"⚠️ 訂閱被撤銷：{sub.get('type')} status={sub.get('status')}")
            return
        if msg_type == "notification":
            event_type, event = event_from_notification(data)
            result = self.on_event(event_type, event)
            if asyncio.iscoroutine(result):
                await result
            return
        self.log(f"ℹ️ EventSub 未處理訊息類型：{msg_type}")


# === ffmpeg_monitor.py ===

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable

from PIL import Image, UnidentifiedImageError


LogFn = Callable[[str], None]


async def simulate_ffmpeg_monitor(
    broadcaster: str,
    log: LogFn,
    stop_event: asyncio.Event,
) -> bool:
    """回傳 True 代表模擬判定「正片開始」。"""
    log(f"🎥 開始持續擷取 {broadcaster} 的 HLS 串流畫面...（模擬模式）")
    for i in range(3, 0, -1):
        if stop_event.is_set():
            log(f"[{broadcaster}] 畫面監控已停止")
            return False
        log(f"[{broadcaster}] 畫面特徵分析中... ({i})")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.5)
            log(f"[{broadcaster}] 畫面監控已停止")
            return False
        except asyncio.TimeoutError:
            pass
    log(f"🚨🚨 [{broadcaster}] 畫面發生劇烈切換，正片開始！")
    return True


async def monitor_broadcast(
    broadcaster: str,
    settings: Settings,
    log: LogFn,
    stop_event: asyncio.Event,
    *,
    already_live: bool,
    pref: ChannelPref | None = None,
    started_at: datetime | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> bool:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        log("❌ 找不到 ffmpeg。請安裝 FFmpeg 並加入 PATH，或把 ffmpeg.exe 放到程式同一個資料夾。")
        return False

    if should_skip_start_detect(started_at, settings.skip_start_after_min):
        log(
            f"ℹ️ [{broadcaster}] 開台已超過 {skip_start_after_label(settings.skip_start_after_min)}，"
            "不再偵測開頭／正片。"
        )
        if on_phase:
            on_phase("skipped")
        return False

    pref = pref or ChannelPref(login=broadcaster)
    ignore_color = parse_ignore_color(pref.ignore_color)
    ignore_tolerance = clamp_ignore_tolerance(pref.ignore_tolerance)
    similarity_pct = clamp_similarity_pct(pref.similarity_pct)
    threshold = similarity_pct_to_threshold(similarity_pct)
    refs, used = load_reference_hashes(
        settings.standby_dir,
        broadcaster,
        ignore_color=ignore_color,
        ignore_tolerance=ignore_tolerance,
    )
    log(f"🎯 [{broadcaster}] 像待命門檻 {similarity_pct}%（差 ≤ {threshold}/{DHASH_BITS}）")
    if ignore_color:
        log(
            f"🎨 [{broadcaster}] 略過顏色 {format_ignore_color(ignore_color)}，容差 {ignore_tolerance}"
        )
    if used:
        log(f"🖼️ [{broadcaster}] 待命參考圖：{', '.join(os.path.basename(p) for p in used)}")
    elif already_live:
        log(
            f"ℹ️ [{broadcaster}] 啟動時已在直播且沒有 standby/{broadcaster}.png，"
            "略過自動判定（避免把正片誤當成待命）。"
        )
        if on_phase:
            on_phase("live")
        return False
    else:
        log(
            f"ℹ️ [{broadcaster}] 沒有待命參考圖，將在略過廣告後嘗試建立穩定 baseline。"
            f"建議放一張截圖到 standby/{broadcaster}.png"
        )

    tmp = tempfile.mkdtemp(prefix=f"standby-{broadcaster}-")
    frame_path = os.path.join(tmp, "frame.jpg")
    procs: list[asyncio.subprocess.Process] = []
    try:
        resolved = await _resolve_with_retry(broadcaster, log, stop_event)
        if resolved is None:
            return False
        log(f"🎥 [{broadcaster}] 取得 {resolved.quality} 串流，開始抽幀…")
        procs = await _start_grabbers(
            ffmpeg, broadcaster, resolved.url, frame_path, settings.frame_interval_sec, log
        )
        if not procs:
            return False
        return await _watch_frames(
            broadcaster,
            settings,
            log,
            stop_event,
            frame_path,
            procs,
            refs,
            threshold=threshold,
            similarity_pct=similarity_pct,
            ignore_color=ignore_color,
            ignore_tolerance=ignore_tolerance,
            started_at=started_at,
            on_phase=on_phase,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log(f"❌ [{broadcaster}] 畫面監控失敗：{exc}")
        return False
    finally:
        await _kill_all(procs)
        shutil.rmtree(tmp, ignore_errors=True)


async def _resolve_with_retry(
    login: str,
    log: LogFn,
    stop_event: asyncio.Event,
    attempts: int = 6,
):
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        if stop_event.is_set():
            return None
        try:
            return await asyncio.to_thread(resolve_twitch_stream, login)
        except StreamResolveError as exc:
            last_error = exc
            log(f"⏳ [{login}] 尚未取得 HLS（{attempt}/{attempts}）：{exc}")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=3)
                return None
            except asyncio.TimeoutError:
                pass
    raise StreamResolveError(str(last_error) if last_error else "無法解析串流")


async def _start_grabbers(
    ffmpeg: str,
    login: str,
    stream_url: str | None,
    frame_path: str,
    interval: float,
    log: LogFn,
) -> list[asyncio.subprocess.Process]:
    if stream_url:
        ff = await _spawn_ffmpeg(ffmpeg, ["-i", stream_url], frame_path, interval)
        return [ff]
    streamlink = find_streamlink_cli()
    if not streamlink:
        log(f"❌ [{login}] 解析結果沒有 URL，且找不到 streamlink 命令列可改走 pipe。")
        return []
    return await _spawn_pipe(ffmpeg, streamlink, login, frame_path, interval)


def _ffmpeg_output_args(frame_path: str, interval: float) -> list[str]:
    fps = 1.0 / max(interval, 1.0)
    return [
        "-an",
        "-vf",
        f"fps={fps}",
        "-q:v",
        "6",
        "-update",
        "1",
        "-y",
        frame_path,
    ]


def _creationflags() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return 0


async def _spawn_ffmpeg(
    ffmpeg: str,
    input_args: list[str],
    frame_path: str,
    interval: float,
) -> asyncio.subprocess.Process:
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "15",
        "-user_agent",
        "Mozilla/5.0",
        *input_args,
        *_ffmpeg_output_args(frame_path, interval),
    ]
    kwargs: dict = {
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.PIPE,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _creationflags()
    else:
        kwargs["start_new_session"] = True
    return await asyncio.create_subprocess_exec(*cmd, **kwargs)


async def _spawn_pipe(
    ffmpeg: str,
    streamlink: str,
    login: str,
    frame_path: str,
    interval: float,
) -> list[asyncio.subprocess.Process]:
    sl_cmd = [
        streamlink,
        "--stdout",
        "--twitch-disable-ads",
        f"https://www.twitch.tv/{login}",
        "480p,360p,160p,worst",
    ]
    flags = _creationflags()
    sl_kwargs: dict = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    ff_kwargs: dict = {
        "stdin": None,
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.PIPE,
    }
    if sys.platform == "win32":
        sl_kwargs["creationflags"] = flags
        ff_kwargs["creationflags"] = flags
    else:
        sl_kwargs["start_new_session"] = True
        ff_kwargs["start_new_session"] = True
    sl = await asyncio.create_subprocess_exec(*sl_cmd, **sl_kwargs)
    ff_cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        *_ffmpeg_output_args(frame_path, interval),
    ]
    ff_kwargs["stdin"] = sl.stdout
    ff = await asyncio.create_subprocess_exec(*ff_cmd, **ff_kwargs)
    if sl.stdout:
        sl.stdout.close()
    return [sl, ff]


async def _watch_frames(
    login: str,
    settings: Settings,
    log: LogFn,
    stop_event: asyncio.Event,
    frame_path: str,
    procs: list[asyncio.subprocess.Process],
    refs: list[int],
    *,
    threshold: int,
    similarity_pct: int,
    ignore_color: tuple[int, int, int] | None,
    ignore_tolerance: int,
    started_at: datetime | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> bool:
    detector = StandbyDetector(refs, threshold, settings.confirm_frames)
    started = time.monotonic()
    last_sig: tuple[float, int] | None = None
    recent: list[int] = []
    ad_logged = False
    stable_logged = False
    standby_logs = 0
    stderr_tasks = [asyncio.create_task(_collect_stderr(proc)) for proc in procs]

    try:
        while not stop_event.is_set():
            dead = [proc for proc in procs if proc.returncode is not None]
            if dead:
                extras = []
                for task in stderr_tasks:
                    if task.done():
                        extras.extend(task.result()[-6:])
                detail = " | ".join(extras) if extras else f"exit={dead[0].returncode}"
                log(f"❌ [{login}] 抽幀行程結束：{detail}")
                return False

            if should_skip_start_detect(started_at, settings.skip_start_after_min):
                log(
                    f"ℹ️ [{login}] 開台已超過 {skip_start_after_label(settings.skip_start_after_min)}，"
                    "停止偵測開頭／正片。"
                )
                if on_phase:
                    on_phase("skipped")
                return False

            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=max(settings.frame_interval_sec / 2, 0.5)
                )
                return False
            except asyncio.TimeoutError:
                pass

            if not os.path.isfile(frame_path) or os.path.getsize(frame_path) == 0:
                if time.monotonic() - started > 90:
                    log(f"❌ [{login}] 90 秒內沒抽到畫面")
                    return False
                continue

            sig = (os.path.getmtime(frame_path), os.path.getsize(frame_path))
            if sig == last_sig:
                continue
            last_sig = sig

            try:
                with Image.open(frame_path) as img:
                    img.load()
                    frame_hash = dhash_int(
                        img,
                        ignore_color=ignore_color,
                        ignore_tolerance=ignore_tolerance,
                    )
            except (OSError, UnidentifiedImageError):
                continue

            elapsed = time.monotonic() - started
            if elapsed < settings.ad_skip_sec:
                if not ad_logged:
                    log(f"⏳ [{login}] 略過開台前 {settings.ad_skip_sec:.0f} 秒（廣告／過場）")
                    ad_logged = True
                continue

            if not detector.references:
                recent.append(frame_hash)
                recent = recent[-5:]
                if hashes_are_stable(recent, threshold):
                    detector.set_references(recent[-3:])
                    log(f"📌 [{login}] 已建立穩定待命 baseline")
                    stable_logged = True
                elif elapsed > 90 and not stable_logged:
                    log(
                        f"⚠️ [{login}] 畫面遲遲不穩定，可能已在正片或待命是動態影片。"
                        f"請改放 standby/{login}.png"
                    )
                    stable_logged = True
                continue

            state, dist = detector.observe(frame_hash)
            bits = DHASH_BITS
            if dist is None:
                continue
            sim = hash_similarity_pct(dist, bits)
            if state == "standby":
                standby_logs += 1
                if standby_logs == 1 or standby_logs % 10 == 0:
                    log(f"[{login}] 與待命相似度 {sim}%（門檻 {similarity_pct}%，差 {dist}/{bits}）像待命")
            elif state == "pending":
                log(
                    f"[{login}] 與待命相似度 {sim}%（門檻 {similarity_pct}%，差 {dist}/{bits}）"
                    f"不像 {detector.unlike_streak}/{settings.confirm_frames}"
                )
            else:
                log(
                    f"🚨🚨 [{login}] 畫面已離開待命（相似度 {sim}%／門檻 {similarity_pct}%），正片開始！"
                )
                return True
        return False
    finally:
        for task in stderr_tasks:
            task.cancel()


async def _collect_stderr(proc: asyncio.subprocess.Process) -> list[str]:
    lines: list[str] = []
    if proc.stderr is None:
        return lines
    try:
        while True:
            raw = await proc.stderr.readline()
            if not raw:
                break
            text = raw.decode("utf-8", "replace").strip()
            if text:
                lines.append(text)
                if len(lines) > 40:
                    lines.pop(0)
    except Exception:
        pass
    return lines


async def _kill_all(procs: list[asyncio.subprocess.Process]) -> None:
    for proc in procs:
        if proc.returncode is not None:
            continue
        try:
            proc.kill()
        except ProcessLookupError:
            continue
    for proc in procs:
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass


# === app ===

__version__ = "0.13.0"

ROW_PHASES: dict[str, tuple[str, str]] = {
    "idle": ("未監控", MUTED),
    "listening": ("聽開台中", BLUE),
    "detecting": ("偵測開頭中", ORANGE),
    "skipped": ("已略過開頭", WARN),
    "live": ("直播中", GREEN),
    "main": ("正片已開始", OK),
    "offline": ("已關台", MUTED),
    "failed": ("偵測失敗", ERR),
    "unsubscribed": ("這次不聽", WARN),
}


def row_phase_style(phase: str) -> tuple[str, str]:
    return ROW_PHASES.get(phase, ROW_PHASES["idle"])


# 固定名字欄寬度，讓每列「開台通知」從同一位置開始。
NAME_COL_PX = 128
NAME_COL_H = 40


class ChannelRow:
    def __init__(
        self,
        master: tk.Widget,
        app: StreamMonitorApp,
        pref: ChannelPref,
        standby_dir: str,
    ) -> None:
        self.app = app
        self.standby_dir = standby_dir
        self.frame = tk.Frame(master, bg=PANEL)
        self.frame.pack(fill=tk.X, pady=4)

        self.login_var = tk.StringVar(value=pref.login)
        self.entry = entry(self.frame, self.login_var, width=16)
        self.entry.pack(side=tk.LEFT, padx=(0, 6), anchor="n")
        self.entry.bind("<FocusOut>", self._on_login_changed)
        self.entry.bind("<Return>", self._on_login_changed)

        self._avatar_photo = None
        self.avatar_label = tk.Label(self.frame, bg=PANEL, width=3)
        self.avatar_label.pack(side=tk.LEFT, padx=(0, 4), anchor="n")

        identity = tk.Frame(self.frame, bg=PANEL, width=NAME_COL_PX, height=NAME_COL_H)
        identity.pack(side=tk.LEFT, padx=(0, 8), anchor="n")
        identity.pack_propagate(False)
        self.name_var = tk.StringVar(value=pref.display_name.strip())
        self.name_label = tk.Label(
            identity,
            textvariable=self.name_var,
            anchor="w",
            bg=PANEL,
            fg=MUTED,
            font=FONT_BOLD,
            wraplength=NAME_COL_PX,
            justify="left",
        )
        self.name_label.pack(anchor="w")
        self.phase = "idle"
        self.phase_label = tk.Label(
            identity,
            text=ROW_PHASES["idle"][0],
            anchor="w",
            bg=PANEL,
            fg=ROW_PHASES["idle"][1],
            font=FONT_SMALL,
        )
        self.phase_label.pack(anchor="w")
        self._named_login = pref.login.lower() if pref.display_name.strip() else ""
        self.refresh_avatar()

        self.notify_live_var = tk.BooleanVar(value=pref.notify_live)
        self.notify_start_var = tk.BooleanVar(value=pref.notify_start)
        self.open_watch_var = tk.BooleanVar(value=pref.open_watch)
        self.close_watch_var = tk.BooleanVar(value=pref.close_watch)
        checks = tk.Frame(self.frame, bg=PANEL)
        checks.pack(side=tk.LEFT, padx=(0, 8), anchor="n")
        self.live_chk = tk.Checkbutton(
            checks,
            text="開台通知",
            variable=self.notify_live_var,
            command=self.app._persist_watchlist,
            bg=PANEL,
            font=FONT,
            activebackground=PANEL,
        )
        self.live_chk.pack(side=tk.LEFT)
        self.start_chk = tk.Checkbutton(
            checks,
            text="開始通知",
            variable=self.notify_start_var,
            command=self.app._persist_watchlist,
            bg=PANEL,
            font=FONT,
            activebackground=PANEL,
        )
        self.start_chk.pack(side=tk.LEFT)
        self.watch_chk = tk.Checkbutton(
            checks,
            text="開網頁",
            variable=self.open_watch_var,
            command=self.app._persist_watchlist,
            bg=PANEL,
            font=FONT,
            activebackground=PANEL,
        )
        self.watch_chk.pack(side=tk.LEFT)
        self.close_chk = tk.Checkbutton(
            checks,
            text="關網頁",
            variable=self.close_watch_var,
            command=self.app._persist_watchlist,
            bg=PANEL,
            font=FONT,
            activebackground=PANEL,
        )
        self.close_chk.pack(side=tk.LEFT)

        tools = tk.Frame(self.frame, bg=PANEL)
        tools.pack(side=tk.LEFT, anchor="n")
        self.img_btn = small_button(tools, "選圖片", self._pick_image, BLUE)
        self.img_btn.pack(side=tk.LEFT, padx=2)
        self.vid_btn = small_button(tools, "選影片", self._pick_video, PURPLE)
        self.vid_btn.pack(side=tk.LEFT, padx=2)

        tk.Label(tools, text="像", bg=PANEL, fg=MUTED, font=FONT).pack(side=tk.LEFT)
        self.similarity_var = tk.StringVar(value=str(pref.similarity_pct))
        self.similarity_spin = tk.Spinbox(
            tools,
            from_=1,
            to=99,
            width=3,
            textvariable=self.similarity_var,
            font=FONT,
            command=self.app._persist_watchlist,
        )
        self.similarity_spin.pack(side=tk.LEFT)
        tk.Label(tools, text="%", bg=PANEL, fg=MUTED, font=FONT).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        self.similarity_var.trace_add("write", lambda *_: self.app._persist_watchlist())

        self.clear_btn = small_button(tools, "清素材", self._clear_media, GRAY)
        self.clear_btn.pack(side=tk.LEFT, padx=2)

        self.ignore_color = pref.ignore_color.strip()
        self.ignore_tol_var = tk.StringVar(value=str(pref.ignore_tolerance))
        self.color_swatch = tk.Label(
            tools,
            text="  ",
            width=2,
            relief=tk.SOLID,
            bd=1,
            bg=PANEL,
        )
        self.color_swatch.pack(side=tk.LEFT, padx=(4, 2))
        self.color_btn = small_button(tools, "略過色", self._pick_ignore_color, ORANGE)
        self.color_btn.pack(side=tk.LEFT, padx=2)
        self.clear_color_btn = small_button(tools, "清色", self._clear_ignore_color, GRAY)
        self.clear_color_btn.pack(side=tk.LEFT, padx=2)
        tk.Label(tools, text="容差", bg=PANEL, fg=MUTED, font=FONT).pack(side=tk.LEFT)
        self.ignore_tol_spin = tk.Spinbox(
            tools,
            from_=0,
            to=120,
            width=3,
            textvariable=self.ignore_tol_var,
            font=FONT,
            command=self.app._persist_watchlist,
        )
        self.ignore_tol_spin.pack(side=tk.LEFT, padx=(0, 4))
        self.ignore_tol_var.trace_add("write", lambda *_: self.app._persist_watchlist())
        self._refresh_swatch()

        self.remove_btn = small_button(tools, "移除", self._remove, RED)
        self.remove_btn.pack(side=tk.LEFT, padx=2)

        self.status = tk.Label(
            tools, text="", anchor="w", bg=PANEL, fg=MUTED, font=FONT_SMALL
        )
        self.status.pack(side=tk.LEFT, padx=(6, 0))
        self.refresh_status()

    def login(self) -> str:
        parsed = parse_logins(self.login_var.get())
        return parsed[0] if parsed else ""

    def to_pref(self) -> ChannelPref | None:
        name = self.login()
        if not name:
            return None
        return ChannelPref(
            login=name,
            notify_live=bool(self.notify_live_var.get()),
            notify_start=bool(self.notify_start_var.get()),
            open_watch=bool(self.open_watch_var.get()),
            close_watch=bool(self.close_watch_var.get()),
            display_name=self._saved_display_name(),
            similarity_pct=clamp_similarity_pct(self.similarity_var.get()),
            ignore_color=self.ignore_color,
            ignore_tolerance=clamp_ignore_tolerance(self.ignore_tol_var.get()),
        )

    def _saved_display_name(self) -> str:
        text = self.name_var.get().strip()
        if text in {"查名字中…", "找不到這台"}:
            return ""
        return text

    def set_display_name(self, name: str, *, missing: bool = False) -> None:
        login = self.login()
        text = name.strip()
        if missing:
            self._named_login = login
            self.name_var.set("找不到這台")
            self.name_label.config(fg=ERR)
            return
        self._named_login = login if text else ""
        self.name_var.set(text)
        self.name_label.config(fg=FG if text else MUTED)
        if not text or missing:
            self.refresh_avatar()

    def refresh_avatar(self) -> None:
        login = self.login()
        path = avatar_cache_path(login) if login else ""
        if login and os.path.isfile(path):
            try:
                from PIL import ImageTk

                with Image.open(path) as img:
                    self._avatar_photo = ImageTk.PhotoImage(img.copy())
                self.avatar_label.config(image=self._avatar_photo, text="", width=AVATAR_SIZE)
                return
            except (OSError, UnidentifiedImageError, tk.TclError):
                pass
        self._avatar_photo = None
        self.avatar_label.config(image="", text="", width=3)

    def _on_login_changed(self, _event=None) -> None:
        login = self.login()
        self.refresh_status()
        if not login:
            self.set_display_name("")
            self.app._persist_watchlist()
            return
        if login == self._named_login and self.name_var.get().strip() not in {
            "",
            "查名字中…",
            "找不到這台",
        }:
            self.refresh_avatar()
            return
        self.set_display_name("查名字中…")
        self.refresh_avatar()
        self.app._schedule_name_lookup((login,))
        self.app._persist_watchlist()

    def set_phase(self, phase: str) -> None:
        self.phase = phase if phase in ROW_PHASES else "idle"
        text, color = row_phase_style(self.phase)
        self.phase_label.config(text=text, fg=color)

    def refresh_status(self) -> None:
        name = self.login()
        if not name:
            self.status.config(text="先填頻道名稱")
            return
        self.status.config(text=describe_references(self.standby_dir, name))

    def set_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in (
            self.entry,
            self.similarity_spin,
            self.color_btn,
            self.clear_color_btn,
            self.ignore_tol_spin,
            self.img_btn,
            self.vid_btn,
            self.clear_btn,
            self.remove_btn,
        ):
            widget.config(state=state)

    def _refresh_swatch(self) -> None:
        color = parse_ignore_color(self.ignore_color)
        self.color_swatch.config(bg=self.ignore_color if color else PANEL)

    def _pick_ignore_color(self) -> None:
        initial = self.ignore_color if parse_ignore_color(self.ignore_color) else "#ffffff"
        _rgb, hexcol = colorchooser.askcolor(color=initial, title="選要略過比較的顏色（標題字等）")
        if not hexcol:
            return
        self.ignore_color = str(hexcol)
        self._refresh_swatch()
        self.app._persist_watchlist()
        self.app.log(f"🎨 [{self.login() or '?'}] 略過顏色 {self.ignore_color}")

    def _clear_ignore_color(self) -> None:
        if not self.ignore_color:
            return
        self.ignore_color = ""
        self._refresh_swatch()
        self.app._persist_watchlist()
        self.app.log(f"🎨 [{self.login() or '?'}] 已取消略過顏色")

    def _pick_image(self) -> None:
        name = self.login()
        if not name:
            self.app.log("❌ 請先填這列的頻道登入名稱，再選待命圖片")
            return
        path = filedialog.askopenfilename(
            title=f"選擇 {name} 的待命圖片",
            filetypes=[
                ("圖片", "*.png *.jpg *.jpeg *.webp *.bmp"),
                ("所有檔案", "*.*"),
            ],
        )
        if not path:
            return
        try:
            files = import_standby_image(self.standby_dir, name, path)
        except Exception as exc:
            self.app.log(f"❌ [{name}] 匯入圖片失敗：{exc}")
            return
        self.refresh_status()
        self.app.log(f"🖼️ [{name}] 已設定待命圖片：{os.path.basename(files[0])}")
        self.app._persist_watchlist()

    def _pick_video(self) -> None:
        name = self.login()
        if not name:
            self.app.log("❌ 請先填這列的頻道登入名稱，再選待命影片")
            return
        path = filedialog.askopenfilename(
            title=f"選擇 {name} 的待命影片",
            filetypes=[
                ("影片", "*.mp4 *.mkv *.webm *.mov *.avi *.m4v"),
                ("所有檔案", "*.*"),
            ],
        )
        if not path:
            return
        self.app.log(f"🎬 [{name}] 正在從影片抽待命幀…")
        try:
            files = import_standby_video(self.standby_dir, name, path)
        except Exception as exc:
            self.app.log(f"❌ [{name}] 匯入影片失敗：{exc}")
            return
        self.refresh_status()
        self.app.log(f"🖼️ [{name}] 已從影片抽出 {len(files)} 張待命樣本")
        self.app._persist_watchlist()

    def _clear_media(self) -> None:
        name = self.login()
        if not name:
            return
        clear_reference_files(self.standby_dir, name)
        self.refresh_status()
        self.app.log(f"🧹 [{name}] 已清除待命素材")

    def _remove(self) -> None:
        self.app._remove_row(self)


class SettingsWindow(tk.Toplevel):
    def __init__(self, app: StreamMonitorApp) -> None:
        super().__init__(app.root)
        self.app = app
        self.title("修改設定")
        self.geometry("560x460")
        self.configure(bg=BG)
        self.transient(app.root)
        current = load_settings()

        box = group(self, "連線與通知")
        box.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        label(box, "這些存在本機 .env，不會上傳 Git。改完按儲存套用即可。").pack(
            anchor="w", pady=(0, 8)
        )

        self.client_id = self._field(box, "Twitch Client ID", current.twitch_client_id, "")
        self.client_secret = self._field(
            box, "Twitch Client Secret（可空）", current.twitch_client_secret, "*"
        )
        self.webhook = self._field(
            box, "Discord Webhook 網址", current.discord_webhook_url, ""
        )
        label(box, "開台超過多久就不再偵測開頭（兩個都 0＝不略過）").pack(
            fill=tk.X, pady=(8, 0)
        )
        skip_row = tk.Frame(box, bg=PANEL)
        skip_row.pack(fill=tk.X, pady=(2, 4))
        hours, mins = skip_start_hms(current.skip_start_after_min)
        self.skip_hours = entry(skip_row, None, width=6)
        self.skip_hours.pack(side=tk.LEFT)
        self.skip_hours.insert(0, str(hours))
        tk.Label(skip_row, text=" 小時 ", bg=PANEL, fg=FG, font=FONT).pack(side=tk.LEFT)
        self.skip_mins = entry(skip_row, None, width=6)
        self.skip_mins.pack(side=tk.LEFT)
        self.skip_mins.insert(0, str(mins))
        tk.Label(skip_row, text=" 分鐘", bg=PANEL, fg=FG, font=FONT).pack(side=tk.LEFT)
        label(
            box,
            "例如 0 小時 46 分，或 1 小時 20 分。剛開台仍會偵測；超過此時長就停抽幀。",
            fg=MUTED,
        ).pack(anchor="w", pady=(0, 8))

        label(
            box,
            "Client ID：Twitch 開發者主控台 → 應用程式 → 管理\n"
            "Webhook：Discord 頻道設定 → 整合 → Webhook → 複製網址",
        ).pack(anchor="w", pady=(4, 8))

        color_button(box, "💾  儲存套用", self._save, ORANGE).pack(fill=tk.X, pady=(4, 0))

    def _field(self, parent: tk.Widget, title: str, value: str, show: str) -> tk.Entry:
        box = tk.Frame(parent, bg=PANEL)
        box.pack(fill=tk.X, pady=4)
        label(box, title).pack(fill=tk.X)
        widget = entry(box, None, show=show)
        widget.pack(fill=tk.X)
        widget.insert(0, value)
        return widget

    def _skip_start_minutes(self) -> int:
        try:
            hours = int((self.skip_hours.get() or "0").strip() or "0")
        except ValueError:
            hours = 0
        try:
            mins = int((self.skip_mins.get() or "0").strip() or "0")
        except ValueError:
            mins = 0
        hours = max(0, hours)
        mins = max(0, mins)
        return clamp_skip_start_after_min(hours * 60 + mins)

    def _save(self) -> None:
        try:
            upsert_env_values(
                {
                    "TWITCH_CLIENT_ID": self.client_id.get().strip(),
                    "TWITCH_CLIENT_SECRET": self.client_secret.get().strip(),
                    "DISCORD_WEBHOOK_URL": self.webhook.get().strip(),
                    "SKIP_START_AFTER_MIN": str(self._skip_start_minutes()),
                }
            )
        except OSError as exc:
            self.app.log(f"❌ 設定儲存失敗：{exc}")
            return
        self.app.refresh_settings_status()
        self.app.log("✅ 設定已儲存。下次按啟動就會用新的 ID / Webhook／開頭偵測時限。")
        self.destroy()


class StreamMonitorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"實況守門員 主控台 V{__version__}")
        self.root.geometry("1180x720")
        apply_root(root)
        self._apply_icon()

        self.msg_queue: queue.Queue[str] = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._stop_requested = False
        self._bg_done = threading.Event()
        self._monitor_tasks: dict[str, asyncio.Task] = {}
        self._active_logins: tuple[str, ...] = ()
        self._active_prefs: dict[str, ChannelPref] = {}
        self._display_names: dict[str, str] = {}
        self._watch = WatchBrowser()
        self.rows: list[ChannelRow] = []

        initial = load_settings()
        self.standby_dir = initial.standby_dir

        header = tk.Frame(root, bg=BG)
        header.pack(fill=tk.X, padx=14, pady=(12, 6))
        self.status_dot = tk.Label(header, text="●", fg=OK, bg=BG, font=FONT_BOLD)
        self.status_dot.pack(side=tk.LEFT)
        self.status_label = tk.Label(
            header, text="狀態：待命", fg=OK, bg=BG, font=FONT_BOLD
        )
        self.status_label.pack(side=tk.LEFT, padx=6)
        self.settings_status = tk.Label(header, text="", fg="#555555", bg=BG, font=FONT)
        self.settings_status.pack(side=tk.LEFT, padx=12)
        color_button(header, "⚙  修改設定", self.open_settings, NAVY).pack(side=tk.RIGHT)
        self.refresh_settings_status()

        watch = group(root, "監看頻道")
        watch.pack(fill=tk.X, padx=14, pady=6)
        label(
            watch,
            "每台可調「像待命」相似度（預設 60%）、略過標題等會變的顏色，並指定待命圖片或影片。開網頁／關網頁分開勾。EventSub 預算 10：只聽開台每台 1，再勾關網頁 +1。顯示名稱下方是這台目前狀態（顏色不同）。名稱太長時可左右拖動這一列，以免看不到移除。",
        ).pack(fill=tk.X, pady=(0, 6))
        self.eventsub_budget = tk.Label(
            watch, text="", anchor="w", bg=PANEL, fg=MUTED, font=FONT
        )
        self.eventsub_budget.pack(fill=tk.X, pady=(0, 6))
        add_row = tk.Frame(watch, bg=PANEL)
        add_row.pack(fill=tk.X, pady=(0, 6))
        self.add_var = tk.StringVar()
        entry(add_row, self.add_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        color_button(add_row, "＋ 新增頻道", self._add_from_entry, ORANGE).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        self.list_canvas, self.list_frame = attach_hscroll(watch)

        prefs = load_channel_prefs()
        if not prefs and initial.user_logins:
            prefs = [ChannelPref(login=login) for login in initial.user_logins]
        if not prefs:
            prefs = [ChannelPref(login="")]
        for pref in prefs:
            self._add_row(pref)
        self._refresh_eventsub_budget()
        self._schedule_name_lookup()

        control = group(root, "監控控制")
        control.pack(fill=tk.X, padx=14, pady=6)
        self.start_btn = color_button(
            control, "▶  啟動自動監控", self.start_system, GREEN
        )
        self.start_btn.pack(fill=tk.X, pady=(0, 6))
        self.stop_btn = color_button(control, "■  停止監控", self.stop_system, RED)
        self.stop_btn.pack(fill=tk.X)
        self.stop_btn.config(state=tk.DISABLED)

        log_box = group(root, "近期事件日誌")
        log_box.pack(fill=tk.BOTH, expand=True, padx=14, pady=(6, 14))
        self.log_area = scrolledtext.ScrolledText(
            log_box,
            height=14,
            state="disabled",
            font=FONT_LOG,
            bg="white",
            relief=tk.SOLID,
            bd=1,
        )
        self.log_area.pack(fill=tk.BOTH, expand=True)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self.process_queue)

    def open_settings(self) -> None:
        SettingsWindow(self)

    def refresh_settings_status(self) -> None:
        settings = load_settings()
        client = "已填" if settings.twitch_client_id else "未填"
        hook = "已填" if settings.discord_webhook_url else "未填"
        skip = skip_start_after_label(settings.skip_start_after_min)
        self.settings_status.config(
            text=f"Client ID：{client}　Discord：{hook}　開頭偵測：{skip}"
        )

    def set_run_status(self, text: str, *, ok: bool = True) -> None:
        color = OK if ok else RED
        self.status_dot.config(fg=color)
        self.status_label.config(text=text, fg=color)

    def _apply_icon(self) -> None:
        ico = get_resource_path("app_master_icon.ico")
        if not os.path.isfile(ico):
            return
        try:
            self.root.iconbitmap(ico)
        except tk.TclError:
            pass

    def _add_from_entry(self) -> None:
        parsed = parse_logins(self.add_var.get())
        if not parsed:
            self.log("❌ 請輸入頻道登入名稱或 twitch.tv 網址")
            return
        existing = {row.login() for row in self.rows}
        for login in parsed:
            if login in existing:
                self.log(f"ℹ️ {login} 已在名單裡")
                continue
            self._add_row(ChannelPref(login=login))
            self._schedule_name_lookup((login,))
        self.add_var.set("")
        self._persist_watchlist()

    def _add_row(self, pref: ChannelPref) -> None:
        row = ChannelRow(self.list_frame, self, pref, self.standby_dir)
        if pref.login and not pref.display_name.strip():
            row.set_display_name("查名字中…")
        self.rows.append(row)
        bind_hscroll_drag(self.list_canvas, row.frame)

    def _remove_row(self, row: ChannelRow) -> None:
        if row in self.rows:
            self.rows.remove(row)
        row.frame.destroy()
        self._persist_watchlist()

    def _set_rows_enabled(self, enabled: bool) -> None:
        for row in self.rows:
            row.set_enabled(enabled)

    def _prefs_from_ui(self) -> list[ChannelPref]:
        prefs: list[ChannelPref] = []
        seen: set[str] = set()
        for row in self.rows:
            pref = row.to_pref()
            if pref is None or pref.login in seen:
                continue
            seen.add(pref.login)
            prefs.append(pref)
        return prefs

    def _logins_from_ui(self) -> tuple[str, ...]:
        return tuple(pref.login for pref in self._prefs_from_ui())

    def _persist_watchlist(self) -> None:
        prefs = self._prefs_from_ui()
        try:
            save_channel_prefs(prefs)
        except OSError as exc:
            self.log(f"⚠️ 無法儲存頻道名單：{exc}")
            return
        self._active_prefs = {pref.login: pref for pref in prefs}
        self._refresh_eventsub_budget()

    def _refresh_eventsub_budget(self) -> None:
        plan = plan_eventsub(self._prefs_from_ui())
        self.eventsub_budget.config(text=describe_eventsub_plan(plan))

    def _schedule_name_lookup(self, logins: tuple[str, ...] | None = None) -> None:
        targets = logins if logins is not None else self._logins_from_ui()
        targets = tuple(login for login in targets if login)
        if not targets:
            return
        threading.Thread(
            target=self._lookup_names_worker,
            args=(targets,),
            daemon=True,
        ).start()

    def _lookup_names_worker(self, logins: tuple[str, ...]) -> None:
        try:
            users, missing = asyncio.run(self._lookup_names_async(logins))
        except Exception as exc:
            self.log(f"⚠️ 查頻道名字失敗：{exc}")
            return
        self.root.after(0, lambda: self._apply_display_names(logins, users, missing))

    async def _lookup_names_async(
        self, logins: tuple[str, ...]
    ) -> tuple[dict[str, TwitchUser], list[str]]:
        settings = load_settings()
        if settings.simulate or not settings.twitch_client_id:
            return {}, []
        async with httpx.AsyncClient() as client:
            token = await helix_token_for_lookup(
                client,
                settings.twitch_client_id,
                settings.twitch_client_secret,
            )
            if not token:
                return {}, []
            users = await resolve_users(
                client,
                settings.twitch_client_id,
                token,
                logins,
            )
            for login, user in users.items():
                if user.profile_image_url:
                    await cache_profile_image(
                        client, user.profile_image_url, avatar_cache_path(login)
                    )
        missing = [login for login in logins if login not in users]
        return users, missing

    def _apply_display_names(
        self,
        requested: tuple[str, ...],
        users: dict[str, TwitchUser],
        missing: list[str],
    ) -> None:
        if not users and not missing:
            for row in self.rows:
                if row.name_var.get().strip() == "查名字中…":
                    row.set_display_name("")
            return
        wanted = set(requested)
        for row in self.rows:
            login = row.login()
            if login not in wanted:
                continue
            user = users.get(login)
            if user:
                row.set_display_name(user.display_name)
                row.refresh_avatar()
                self._display_names[login] = user.display_name or login
            elif login in missing:
                row.set_display_name("", missing=True)
                row.refresh_avatar()
        self._persist_watchlist()

    def log(self, message: str) -> None:
        self.msg_queue.put(message)

    def process_queue(self) -> None:
        while not self.msg_queue.empty():
            msg = self.msg_queue.get()
            self.log_area.config(state="normal")
            stamp = datetime.now().strftime("%H:%M:%S")
            self.log_area.insert(tk.END, f"[{stamp}] {msg}\n")
            self.log_area.see(tk.END)
            self.log_area.config(state="disabled")
        if self._bg_done.is_set():
            self._bg_done.clear()
            self._reset_buttons()
        self.root.after(100, self.process_queue)

    def _reset_buttons(self) -> None:
        self.start_btn.config(state=tk.NORMAL, text="▶  啟動自動監控")
        self.stop_btn.config(state=tk.DISABLED)
        self._set_rows_enabled(True)
        self.set_run_status("狀態：已停止")
        self._set_all_phases("idle")

    def _row_for_login(self, login: str) -> ChannelRow | None:
        handle = (login or "").strip().lower()
        for row in self.rows:
            if row.login() == handle:
                return row
        return None

    def _apply_phase(self, login: str, phase: str) -> None:
        row = self._row_for_login(login)
        if row:
            row.set_phase(phase)

    def _set_phase(self, login: str, phase: str) -> None:
        self.root.after(0, lambda lg=login, ph=phase: self._apply_phase(lg, ph))

    def _set_all_phases(self, phase: str) -> None:
        for row in self.rows:
            if row.login():
                row.set_phase(phase)

    def _fail_if_detecting(self, login: str) -> None:
        row = self._row_for_login(login)
        if row and row.phase == "detecting":
            row.set_phase("failed")

    def start_system(self) -> None:
        logins = self._logins_from_ui()
        if not logins:
            self.log("❌ 請至少新增一個頻道")
            return
        plan = plan_eventsub(self._prefs_from_ui())
        self.log(describe_eventsub_plan(plan))
        if plan.skipped:
            self.log(f"⚠️ 預算用完，這次不聽：{', '.join(plan.skipped)}")
        self._persist_watchlist()
        self._active_logins = logins
        self._active_prefs = {pref.login: pref for pref in self._prefs_from_ui()}
        self._set_rows_enabled(False)
        self._stop_requested = False
        self.start_btn.config(state=tk.DISABLED, text="監控運行中...")
        self.stop_btn.config(state=tk.NORMAL)
        self.set_run_status("狀態：監控中")
        for row in self.rows:
            login = row.login()
            if not login:
                continue
            if login in plan.skipped:
                row.set_phase("unsubscribed")
            else:
                row.set_phase("listening")
        self.log("☑️ 系統啟動，準備進入背景執行緒...")
        self.log(f"👀 將監看：{', '.join(logins)}")
        threading.Thread(target=self.run_asyncio_loop, daemon=True).start()

    def stop_system(self) -> None:
        self._stop_requested = True
        self.log("正在停止監控…")
        loop = self._loop
        event = self._stop_event
        if loop is not None and event is not None:
            loop.call_soon_threadsafe(event.set)

    def _on_close(self) -> None:
        self.stop_system()
        self.root.destroy()

    def run_asyncio_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.async_main_core())
        except Exception as exc:
            self.log(f"❌ 背景執行緒錯誤：{exc}")
        finally:
            self._loop = None
            self._stop_event = None
            self._bg_done.set()
            loop.close()

    async def async_main_core(self) -> None:
        stop_event = asyncio.Event()
        self._stop_event = stop_event
        if self._stop_requested:
            stop_event.set()
            return

        settings = load_settings().with_logins(self._active_logins)
        if settings.simulate:
            await self._run_simulate(settings, stop_event)
            return

        if not settings.twitch_client_id:
            self.log("❌ 缺少 TWITCH_CLIENT_ID。請複製 .env.example 為 .env 後填入，或設 SIMULATE=1 跑示範。")
            return
        if not settings.user_logins:
            self.log("❌ 請在視窗裡新增要監看的頻道。")
            return

        await self._run_eventsub(settings, stop_event)

    async def _run_simulate(self, settings: Settings, stop_event: asyncio.Event) -> None:
        login = settings.user_logins[0]
        self.log("✅ 模擬模式（SIMULATE=1）：不連 Twitch")
        self.log("✅ Twitch WebSocket 監聽模組已啟動")
        async with httpx.AsyncClient() as http:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2)
                return
            except asyncio.TimeoutError:
                pass
            self.log(f"🚨 收到 EventSub 通知：{login} 開台了！")
            await self._start_monitor(login, settings, http, stop_event)
            await stop_event.wait()
            self._cancel_all_monitors()

    async def _run_eventsub(self, settings: Settings, stop_event: asyncio.Event) -> None:
        self.log(f"👀 監看頻道：{', '.join(settings.user_logins)}")
        async with httpx.AsyncClient() as http:
            try:
                token = await ensure_user_token(
                    http,
                    settings.twitch_client_id,
                    settings.twitch_client_secret,
                    stop_event,
                    self.log,
                )
            except TwitchAuthError as exc:
                self.log(f"❌ Twitch 登入失敗：{exc}")
                return
            if stop_event.is_set():
                return

            users = await resolve_users(
                http,
                settings.twitch_client_id,
                token.access_token,
                settings.user_logins,
            )
            missing = [login for login in settings.user_logins if login not in users]
            for login in missing:
                self.log(f"⚠️ 找不到 Twitch 使用者：{login}")
            if not users:
                self.log("❌ 沒有任何有效頻道可訂閱")
                return

            user_ids = {login: user.user_id for login, user in users.items()}
            self._display_names = {
                login: user.display_name or login for login, user in users.items()
            }
            found = dict(users)
            self.root.after(
                0,
                lambda: self._apply_display_names(tuple(found), found, []),
            )
            id_to_login = {user.user_id: login for login, user in users.items()}
            ordered = [login for login in settings.user_logins if login in user_ids]
            plan = plan_eventsub(
                [
                    self._active_prefs.get(login) or ChannelPref(login=login)
                    for login in ordered
                ]
            )
            eventsub_ids = {login: user_ids[login] for login in plan.included}
            events_by_login = {
                login: eventsub_types(
                    self._active_prefs.get(login) or ChannelPref(login=login)
                )
                for login in plan.included
            }
            skipped = list(plan.skipped)
            live_started = await live_streams(
                http,
                settings.twitch_client_id,
                token.access_token,
                list(user_ids.values()),
            )
            for uid, started_at in live_started.items():
                login = id_to_login.get(uid)
                if login:
                    await self._channel_went_live(
                        login,
                        settings,
                        http,
                        stop_event,
                        source="啟動掃描",
                        already_live=True,
                        started_at=started_at,
                    )

            async def on_event(event_type: str, event: dict) -> None:
                login = str(event.get("broadcaster_user_login") or "").lower()
                name = str(event.get("broadcaster_user_name") or login)
                if name:
                    self._display_names[login] = name
                    self.root.after(
                        0,
                        lambda lg=login, nm=name: self._apply_display_names(
                            (lg,),
                            {
                                lg: TwitchUser(
                                    login=lg, user_id="", display_name=nm
                                )
                            },
                            [],
                        ),
                    )
                if event_type == "stream.online":
                    await self._channel_went_live(
                        login,
                        settings,
                        http,
                        stop_event,
                        source="EventSub",
                        already_live=False,
                        started_at=parse_helix_time(event.get("started_at")),
                    )
                elif event_type == "stream.offline":
                    await self._channel_went_offline(login)

            client = EventSubClient(
                http=http,
                client_id=settings.twitch_client_id,
                access_token=token.access_token,
                user_ids=eventsub_ids,
                log=self.log,
                on_event=on_event,
                events_by_login=events_by_login,
            )
            if eventsub_ids:
                detail = [
                    f"{login}({'/'.join(events_by_login[login]).replace('stream.', '')})"
                    for login in eventsub_ids
                ]
                self.log(f"⚡ EventSub {plan.cost}/{plan.budget}：{', '.join(detail)}")
            if skipped:
                self.log(f"⚠️ 預算用完，這次不聽：{', '.join(skipped)}")
            self.log("✅ Twitch EventSub 已啟動")
            await client.run(stop_event)
            self._cancel_all_monitors()

    async def _channel_went_live(
        self,
        login: str,
        settings: Settings,
        http: httpx.AsyncClient,
        stop_event: asyncio.Event,
        *,
        source: str,
        already_live: bool,
        started_at: datetime | None = None,
    ) -> None:
        existing = self._monitor_tasks.get(login)
        if existing and not existing.done():
            return
        name = self._display_names.get(login, login)
        pref = self._active_prefs.get(login) or ChannelPref(login=login)
        if already_live:
            self.log(f"🔴 啟動時已在直播：{name} ({login})")
        else:
            self.log(f"🚨 {source}：{name} ({login}) 開台了！")
            if pref.notify_live:
                await send_webhook(
                    settings.discord_webhook_url,
                    build_live_message(name, login),
                    client=http,
                    log=self.log,
                )
            else:
                self.log(f"ℹ️ [{login}] 開台通知已關閉")
        if pref.open_watch:
            await self._open_watch_page(login)
        await self._start_monitor(
            login,
            settings,
            http,
            stop_event,
            already_live=already_live,
            started_at=started_at,
        )

    async def _start_monitor(
        self,
        login: str,
        settings: Settings,
        http: httpx.AsyncClient,
        stop_event: asyncio.Event,
        already_live: bool = False,
        started_at: datetime | None = None,
    ) -> None:
        pref = self._active_prefs.get(login) or ChannelPref(login=login)
        if not pref.notify_start:
            self.log(f"ℹ️ [{login}] 開始通知已關閉，不抽幀判定")
            self._set_phase(login, "live")
            return
        if should_skip_start_detect(started_at, settings.skip_start_after_min):
            self.log(
                f"ℹ️ [{login}] 開台已超過 {skip_start_after_label(settings.skip_start_after_min)}，"
                "不再偵測開頭／正片"
            )
            self._set_phase(login, "skipped")
            return
        existing = self._monitor_tasks.get(login)
        if existing and not existing.done():
            self.log(f"ℹ️ {login} 已在監控中，略過重複啟動")
            return
        self._set_phase(login, "detecting")
        self._monitor_tasks[login] = asyncio.create_task(
            self._monitor_wrapper(
                login,
                settings,
                http,
                stop_event,
                already_live=already_live,
                started_at=started_at,
            )
        )

    async def _open_watch_page(self, login: str) -> None:
        opened = await asyncio.to_thread(self._watch.open_channel, login)
        if opened:
            if login in self._watch.used_system_fallback:
                self.log(f"🌐 [{login}] 已用系統瀏覽器打開（關台時可能關不掉分頁）")
            else:
                self.log(f"🌐 [{login}] 已打開頻道頁")
        else:
            self.log(f"⚠️ [{login}] 打不開瀏覽器，請自行開 {twitch_channel_url(login)}")

    async def _channel_went_offline(self, login: str) -> None:
        name = self._display_names.get(login, login)
        self.log(f"⚫ {name} ({login}) 關台了")
        self._set_phase(login, "offline")
        self._cancel_monitor(login)
        pref = self._active_prefs.get(login) or ChannelPref(login=login)
        if not pref.close_watch:
            return
        had_page = self._watch.has_page(login)
        closed = await asyncio.to_thread(self._watch.close_channel, login)
        if closed:
            self.log(f"🪟 [{login}] 已關掉頻道頁")
        elif had_page:
            self.log(f"⚠️ [{login}] 這頁是系統瀏覽器開的，請自行關掉分頁")

    def _cancel_monitor(self, login: str) -> None:
        task = self._monitor_tasks.pop(login, None)
        if task and not task.done():
            task.cancel()

    def _cancel_all_monitors(self) -> None:
        for login in list(self._monitor_tasks):
            self._cancel_monitor(login)
        for pref in self._active_prefs.values():
            if pref.close_watch:
                self._watch.close_channel(pref.login)

    async def _monitor_wrapper(
        self,
        login: str,
        settings: Settings,
        http: httpx.AsyncClient,
        stop_event: asyncio.Event,
        already_live: bool = False,
        started_at: datetime | None = None,
    ) -> None:
        try:
            if settings.simulate:
                started = await simulate_ffmpeg_monitor(login, self.log, stop_event)
            else:
                started = await monitor_broadcast(
                    login,
                    settings,
                    self.log,
                    stop_event,
                    already_live=already_live,
                    pref=self._active_prefs.get(login) or ChannelPref(login=login),
                    started_at=started_at,
                    on_phase=lambda ph, lg=login: self._set_phase(lg, ph),
                )
            if started:
                self._set_phase(login, "main")
                if not stop_event.is_set():
                    pref = self._active_prefs.get(login) or ChannelPref(login=login)
                    if pref.notify_start:
                        display = self._display_names.get(login, login)
                        await send_webhook(
                            settings.discord_webhook_url,
                            build_start_message(display, login),
                            client=http,
                            log=self.log,
                        )
            elif not stop_event.is_set():
                self.root.after(0, lambda lg=login: self._fail_if_detecting(lg))
        except asyncio.CancelledError:
            self.log(f"[{login}] 監控任務已取消")
        finally:
            self._monitor_tasks.pop(login, None)


if __name__ == "__main__":
    root = tk.Tk()
    StreamMonitorApp(root)
    root.mainloop()
