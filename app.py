"""實況守門員 — Twitch EventSub + 畫面抽幀判定 (v0.3.0)"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
import tkinter as tk
from tkinter import scrolledtext

import httpx

from config import Settings, load_settings
from discord_notify import send_webhook
from ffmpeg_monitor import monitor_broadcast, simulate_ffmpeg_monitor
from paths import get_resource_path
from twitch_eventsub import EventSubClient
from twitch_helix import live_user_ids, resolve_users
from twitch_oauth import TwitchAuthError, ensure_user_token

__version__ = "0.3.0"


class StreamMonitorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"實況守門員 v{__version__}")
        self.root.geometry("560x420")
        self._apply_icon()

        self.msg_queue: queue.Queue[str] = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._stop_requested = False
        self._bg_done = threading.Event()
        self._monitor_tasks: dict[str, asyncio.Task] = {}

        btn_row = tk.Frame(root)
        btn_row.pack(pady=10)

        self.start_btn = tk.Button(
            btn_row,
            text="🚀 啟動自動監控",
            font=("Arial", 12),
            command=self.start_system,
        )
        self.start_btn.pack(side=tk.LEFT, padx=6)

        self.stop_btn = tk.Button(
            btn_row,
            text="停止",
            font=("Arial", 12),
            command=self.stop_system,
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=6)

        self.log_area = scrolledtext.ScrolledText(
            root, width=68, height=18, state="disabled"
        )
        self.log_area.pack(pady=10)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self.process_queue)

    def _apply_icon(self) -> None:
        ico = get_resource_path("app_master_icon.ico")
        if not os.path.isfile(ico):
            return
        try:
            self.root.iconbitmap(ico)
        except tk.TclError:
            pass

    def log(self, message: str) -> None:
        self.msg_queue.put(message)

    def process_queue(self) -> None:
        while not self.msg_queue.empty():
            msg = self.msg_queue.get()
            self.log_area.config(state="normal")
            self.log_area.insert(tk.END, msg + "\n")
            self.log_area.see(tk.END)
            self.log_area.config(state="disabled")
        if self._bg_done.is_set():
            self._bg_done.clear()
            self._reset_buttons()
        self.root.after(100, self.process_queue)

    def _reset_buttons(self) -> None:
        self.start_btn.config(state=tk.NORMAL, text="🚀 啟動自動監控")
        self.stop_btn.config(state=tk.DISABLED)

    def start_system(self) -> None:
        self._stop_requested = False
        self.start_btn.config(state=tk.DISABLED, text="監控運行中...")
        self.stop_btn.config(state=tk.NORMAL)
        self.log("系統啟動，準備進入背景執行緒...")
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

        settings = load_settings()
        if settings.simulate:
            await self._run_simulate(settings, stop_event)
            return

        if not settings.twitch_client_id:
            self.log("❌ 缺少 TWITCH_CLIENT_ID。請複製 .env.example 為 .env 後填入，或設 SIMULATE=1 跑示範。")
            return
        if not settings.user_logins:
            self.log("❌ 缺少 TWITCH_USER_LOGINS（逗號分隔的頻道登入名稱）。")
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

            mapping = await resolve_users(
                http,
                settings.twitch_client_id,
                token.access_token,
                settings.user_logins,
            )
            missing = [login for login in settings.user_logins if login not in mapping]
            for login in missing:
                self.log(f"⚠️ 找不到 Twitch 使用者：{login}")
            if not mapping:
                self.log("❌ 沒有任何有效頻道可訂閱")
                return

            id_to_login = {uid: login for login, uid in mapping.items()}
            live_ids = await live_user_ids(
                http,
                settings.twitch_client_id,
                token.access_token,
                list(mapping.values()),
            )
            for uid in live_ids:
                login = id_to_login.get(uid, uid)
                self.log(f"🔴 啟動時已在直播：{login}")
                await self._start_monitor(
                    login, settings, http, stop_event, already_live=True
                )

            async def on_event(event_type: str, event: dict) -> None:
                login = str(event.get("broadcaster_user_login") or "").lower()
                name = event.get("broadcaster_user_name") or login
                if event_type == "stream.online":
                    self.log(f"🚨 EventSub：{name} ({login}) 開台了！")
                    await self._start_monitor(
                        login, settings, http, stop_event, already_live=False
                    )
                elif event_type == "stream.offline":
                    self.log(f"⚪ EventSub：{name} ({login}) 已下播")
                    self._cancel_monitor(login)

            client = EventSubClient(
                http=http,
                client_id=settings.twitch_client_id,
                access_token=token.access_token,
                user_ids=mapping,
                log=self.log,
                on_event=on_event,
            )
            self.log("✅ Twitch EventSub WebSocket 監聽模組已啟動")
            await client.run(stop_event)
            self._cancel_all_monitors()

    async def _start_monitor(
        self,
        login: str,
        settings: Settings,
        http: httpx.AsyncClient,
        stop_event: asyncio.Event,
        already_live: bool = False,
    ) -> None:
        existing = self._monitor_tasks.get(login)
        if existing and not existing.done():
            self.log(f"ℹ️ {login} 已在監控中，略過重複啟動")
            return
        self._monitor_tasks[login] = asyncio.create_task(
            self._monitor_wrapper(
                login, settings, http, stop_event, already_live=already_live
            )
        )

    def _cancel_monitor(self, login: str) -> None:
        task = self._monitor_tasks.pop(login, None)
        if task and not task.done():
            task.cancel()

    def _cancel_all_monitors(self) -> None:
        for login in list(self._monitor_tasks):
            self._cancel_monitor(login)

    async def _monitor_wrapper(
        self,
        login: str,
        settings: Settings,
        http: httpx.AsyncClient,
        stop_event: asyncio.Event,
        already_live: bool = False,
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
                )
            if started and not stop_event.is_set():
                await send_webhook(
                    settings.discord_webhook_url,
                    f"🚨 [{login}] 畫面已離開待命，正片開始！",
                    client=http,
                    log=self.log,
                )
        except asyncio.CancelledError:
            self.log(f"[{login}] 監控任務已取消")
        finally:
            self._monitor_tasks.pop(login, None)


if __name__ == "__main__":
    root = tk.Tk()
    StreamMonitorApp(root)
    root.mainloop()
