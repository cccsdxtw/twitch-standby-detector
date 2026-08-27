"""從 .env 讀取設定。秘密與 token 不進 Git。"""

from __future__ import annotations

import os
from dataclasses import dataclass

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

    @property
    def ready_for_eventsub(self) -> bool:
        return bool(self.twitch_client_id) and not self.simulate


def parse_logins(raw: str) -> tuple[str, ...]:
    parts = []
    seen = set()
    for item in raw.replace(";", ",").split(","):
        login = item.strip().lower()
        if login and login not in seen:
            seen.add(login)
            parts.append(login)
    return tuple(parts)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_settings() -> Settings:
    env_path = os.path.join(app_dir(), ".env")
    load_dotenv(env_path, override=False)

    simulate = _truthy(os.getenv("SIMULATE", "0"))
    logins = parse_logins(os.getenv("TWITCH_USER_LOGINS", ""))
    if simulate and not logins:
        logins = (DEFAULT_SIMULATE_LOGIN,)

    return Settings(
        twitch_client_id=os.getenv("TWITCH_CLIENT_ID", "").strip(),
        twitch_client_secret=os.getenv("TWITCH_CLIENT_SECRET", "").strip(),
        user_logins=logins,
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
        simulate=simulate,
    )
