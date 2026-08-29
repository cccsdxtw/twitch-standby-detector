"""實況守門員 — Twitch EventSub + 畫面抽幀判定 (v0.5.0)"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext

import httpx

from config import Settings, load_settings, parse_logins, save_watchlist, upsert_env_values
from discord_notify import send_webhook
from ffmpeg_monitor import monitor_broadcast, simulate_ffmpeg_monitor
from paths import get_resource_path
from standby import (
    clear_reference_files,
    describe_references,
    import_standby_image,
    import_standby_video,
)
from twitch_eventsub import EventSubClient
from twitch_helix import live_user_ids, resolve_users
from twitch_oauth import TwitchAuthError, ensure_user_token

__version__ = "0.5.0"


class ChannelRow:
    def __init__(
        self,
        master: tk.Widget,
        app: StreamMonitorApp,
        login: str,
        standby_dir: str,
    ) -> None:
        self.app = app
        self.standby_dir = standby_dir
        self.frame = tk.Frame(master)
        self.frame.pack(fill=tk.X, pady=3)

        self.login_var = tk.StringVar(value=login)
        self.entry = tk.Entry(self.frame, textvariable=self.login_var, width=18)
        self.entry.pack(side=tk.LEFT, padx=(0, 6))

        self.status = tk.Label(self.frame, text="", anchor="w", width=22)
        self.status.pack(side=tk.LEFT, padx=(0, 6))

        self.img_btn = tk.Button(self.frame, text="選圖片", command=self._pick_image)
        self.img_btn.pack(side=tk.LEFT, padx=2)
        self.vid_btn = tk.Button(self.frame, text="選影片", command=self._pick_video)
        self.vid_btn.pack(side=tk.LEFT, padx=2)
        self.clear_btn = tk.Button(self.frame, text="清素材", command=self._clear_media)
        self.clear_btn.pack(side=tk.LEFT, padx=2)
        self.remove_btn = tk.Button(self.frame, text="移除", command=self._remove)
        self.remove_btn.pack(side=tk.LEFT, padx=2)
        self.refresh_status()

    def login(self) -> str:
        parsed = parse_logins(self.login_var.get())
        return parsed[0] if parsed else ""

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
            self.img_btn,
            self.vid_btn,
            self.clear_btn,
            self.remove_btn,
        ):
            widget.config(state=state)

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
        self.title("設定")
        self.geometry("560x320")
        self.transient(app.root)
        current = load_settings()

        tk.Label(self, text="這些存在本機 .env，不會上傳 Git。改完按儲存即可。").pack(
            anchor="w", padx=12, pady=(12, 8)
        )

        self.client_id = self._field(
            "Twitch Client ID", current.twitch_client_id, show=""
        )
        self.client_secret = self._field(
            "Twitch Client Secret（可空）", current.twitch_client_secret, show="*"
        )
        self.webhook = self._field(
            "Discord Webhook 網址", current.discord_webhook_url, show=""
        )

        tk.Label(
            self,
            text="Client ID：Twitch 開發者主控台 → 應用程式 → 管理\n"
            "Webhook：Discord 頻道設定 → 整合 → Webhook → 複製網址",
            justify="left",
            fg="#444444",
        ).pack(anchor="w", padx=12, pady=(4, 8))

        btns = tk.Frame(self)
        btns.pack(pady=8)
        tk.Button(btns, text="儲存", command=self._save, width=10).pack(
            side=tk.LEFT, padx=6
        )
        tk.Button(btns, text="關閉", command=self.destroy, width=10).pack(
            side=tk.LEFT, padx=6
        )

    def _field(self, label: str, value: str, show: str) -> tk.Entry:
        box = tk.Frame(self)
        box.pack(fill=tk.X, padx=12, pady=4)
        tk.Label(box, text=label, anchor="w").pack(fill=tk.X)
        entry = tk.Entry(box, show=show)
        entry.pack(fill=tk.X)
        entry.insert(0, value)
        return entry

    def _save(self) -> None:
        try:
            upsert_env_values(
                {
                    "TWITCH_CLIENT_ID": self.client_id.get().strip(),
                    "TWITCH_CLIENT_SECRET": self.client_secret.get().strip(),
                    "DISCORD_WEBHOOK_URL": self.webhook.get().strip(),
                }
            )
        except OSError as exc:
            self.app.log(f"❌ 設定儲存失敗：{exc}")
            return
        self.app.refresh_settings_status()
        self.app.log("✅ 設定已儲存。下次按啟動就會用新的 ID / Webhook。")
        self.destroy()


class StreamMonitorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"實況守門員 v{__version__}")
        self.root.geometry("760x620")
        self._apply_icon()

        self.msg_queue: queue.Queue[str] = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._stop_requested = False
        self._bg_done = threading.Event()
        self._monitor_tasks: dict[str, asyncio.Task] = {}
        self._active_logins: tuple[str, ...] = ()
        self.rows: list[ChannelRow] = []

        initial = load_settings()
        self.standby_dir = initial.standby_dir

        top = tk.Frame(root)
        top.pack(fill=tk.X, padx=12, pady=(10, 0))
        tk.Label(
            top,
            text="每台一列：填頻道名稱，並指定待命圖片或待命影片（影片會抽幾幀當樣本）",
            anchor="w",
        ).pack(fill=tk.X)

        add_row = tk.Frame(root)
        add_row.pack(fill=tk.X, padx=12, pady=(6, 0))
        self.add_var = tk.StringVar()
        tk.Entry(add_row, textvariable=self.add_var, width=22).pack(side=tk.LEFT)
        tk.Button(add_row, text="新增頻道", command=self._add_from_entry).pack(
            side=tk.LEFT, padx=6
        )
        tk.Button(add_row, text="設定 ID / 網址", command=self.open_settings).pack(
            side=tk.LEFT, padx=6
        )
        self.settings_status = tk.Label(add_row, text="", fg="#333333")
        self.settings_status.pack(side=tk.LEFT, padx=10)
        self.refresh_settings_status()

        self.list_frame = tk.Frame(root)
        self.list_frame.pack(fill=tk.X, padx=12, pady=(8, 0))

        logins = initial.user_logins or ("",)
        for login in logins:
            self._add_row(login)

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
            root, width=88, height=16, state="disabled"
        )
        self.log_area.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self.process_queue)

    def open_settings(self) -> None:
        SettingsWindow(self)

    def refresh_settings_status(self) -> None:
        settings = load_settings()
        client = "已填" if settings.twitch_client_id else "未填"
        hook = "已填" if settings.discord_webhook_url else "未填"
        self.settings_status.config(text=f"Twitch Client ID：{client}　Discord：{hook}")

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
            self._add_row(login)
        self.add_var.set("")
        self._persist_watchlist()

    def _add_row(self, login: str) -> None:
        self.rows.append(ChannelRow(self.list_frame, self, login, self.standby_dir))

    def _remove_row(self, row: ChannelRow) -> None:
        if row in self.rows:
            self.rows.remove(row)
        row.frame.destroy()
        self._persist_watchlist()

    def _set_rows_enabled(self, enabled: bool) -> None:
        for row in self.rows:
            row.set_enabled(enabled)

    def _logins_from_ui(self) -> tuple[str, ...]:
        logins: list[str] = []
        seen: set[str] = set()
        for row in self.rows:
            login = row.login()
            if login and login not in seen:
                seen.add(login)
                logins.append(login)
        return tuple(logins)

    def _persist_watchlist(self) -> None:
        try:
            save_watchlist(self._logins_from_ui())
        except OSError as exc:
            self.log(f"⚠️ 無法儲存頻道名單：{exc}")

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
        self._set_rows_enabled(True)

    def start_system(self) -> None:
        logins = self._logins_from_ui()
        if not logins:
            self.log("❌ 請至少新增一個頻道")
            return
        if len(logins) > 5:
            self.log(
                f"⚠️ 目前 {len(logins)} 台；EventSub 每台佔 2 點成本、上限約 10，超過 5 台可能訂閱失敗。"
            )
        self._persist_watchlist()
        self._active_logins = logins
        self._set_rows_enabled(False)
        self._stop_requested = False
        self.start_btn.config(state=tk.DISABLED, text="監控運行中...")
        self.stop_btn.config(state=tk.NORMAL)
        self.log("系統啟動，準備進入背景執行緒...")
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
