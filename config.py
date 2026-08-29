"""從 .env 讀取設定。秘密與 token 不進 Git。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace

from dotenv import load_dotenv

from paths import app_dir

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

    @property
    def ready_for_eventsub(self) -> bool:
        return bool(self.twitch_client_id) and not self.simulate

    def with_logins(self, logins: tuple[str, ...]) -> Settings:
        return replace(self, user_logins=logins)


def watchlist_path() -> str:
    return os.path.join(app_dir(), "watchlist.txt")


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
    with open(watchlist_path(), "w", encoding="utf-8") as handle:
        handle.write("\n".join(logins))
        if logins:
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


def load_settings() -> Settings:
    load_dotenv(env_path(), override=True)

    simulate = _truthy(os.getenv("SIMULATE", "0"))
    logins = parse_logins(load_watchlist_text()) or parse_logins(
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
    )
