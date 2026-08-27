"""畫面比對任務。v0.2.0 仍為模擬，等 EventSub 開台後才啟動。"""

from __future__ import annotations

import asyncio
from typing import Callable

LogFn = Callable[[str], None]


async def simulate_ffmpeg_monitor(
    broadcaster: str,
    log: LogFn,
    stop_event: asyncio.Event,
) -> bool:
    """回傳 True 代表模擬判定「正片開始」。"""
    log(f"🎥 開始持續擷取 {broadcaster} 的 HLS 串流畫面...（目前為模擬）")
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
