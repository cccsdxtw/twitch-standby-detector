"""Twitch EventSub WebSocket 用戶端。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import websockets

from twitch_helix import create_eventsub_subscription

WS_URL = "wss://eventsub.wss.twitch.tv/ws"
EVENT_TYPES = ("stream.online", "stream.offline")

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
    ) -> None:
        self.http = http
        self.client_id = client_id
        self.access_token = access_token
        self.user_ids = user_ids
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
            for event_type in EVENT_TYPES:
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
        cost_note = len(self.user_ids) * len(EVENT_TYPES)
        if cost_note > 10:
            self.log("⚠️ WebSocket 訂閱成本上限約為 10；頻道太多可能訂閱失敗。")

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
