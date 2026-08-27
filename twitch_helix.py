"""Twitch Helix：查使用者、目前開台、建立 EventSub 訂閱。"""

from __future__ import annotations

from typing import Any

import httpx

HELIX = "https://api.twitch.tv/helix"


class TwitchAPIError(RuntimeError):
    pass


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
) -> dict[str, str]:
    """login -> user_id。找不到的 login 不會出現在結果裡。"""
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
    mapping: dict[str, str] = {}
    for user in payload.get("data") or []:
        login = str(user.get("login", "")).lower()
        uid = str(user.get("id", ""))
        if login and uid:
            mapping[login] = uid
    return mapping


async def live_user_ids(
    client: httpx.AsyncClient,
    client_id: str,
    access_token: str,
    user_ids: list[str],
) -> set[str]:
    if not user_ids:
        return set()
    params = [("user_id", uid) for uid in user_ids]
    response = await client.get(
        f"{HELIX}/streams",
        headers=_headers(client_id, access_token),
        params=params,
        timeout=30.0,
    )
    payload = await _json(response)
    live: set[str] = set()
    for row in payload.get("data") or []:
        uid = str(row.get("user_id", ""))
        if uid:
            live.add(uid)
    return live


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
