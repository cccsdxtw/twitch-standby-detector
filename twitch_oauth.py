"""Twitch Device Code 登入與 token 刷新（EventSub WebSocket 需要使用者 access token）。"""

from __future__ import annotations

import asyncio
import json
import os
import time
import webbrowser
from dataclasses import dataclass
from typing import Callable

import httpx

from paths import app_dir

TOKEN_FILENAME = "twitch_token.json"
DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# stream.online / stream.offline 不需要額外 scope；空字串代表「無 scope 的使用者 token」。
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
