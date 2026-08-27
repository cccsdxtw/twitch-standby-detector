"""Discord Incoming Webhook 通知。"""

from __future__ import annotations

from typing import Callable

import httpx

LogFn = Callable[[str], None]


def build_webhook_body(content: str) -> dict:
    return {"content": content[:2000]}


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
