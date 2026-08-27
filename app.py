"""實況守門員 — Twitch standby / 正片切換監控 (v0.1.0)

Tkinter UI + 背景 thread 跑 asyncio 事件迴圈，透過 Queue 安全更新畫面。
目前為骨架：模擬 EventSub 開台通知與 FFmpeg 畫面比對流程。
"""

import asyncio
import queue
import threading
import tkinter as tk
from tkinter import scrolledtext

__version__ = "0.1.0"


class StreamMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"實況守門員 v{__version__}")
        self.root.geometry("500x350")

        # 1. 建立通訊用的安全佇列 (Queue)
        self.msg_queue = queue.Queue()

        # 2. 建立 UI 元件
        self.start_btn = tk.Button(
            root,
            text="🚀 啟動自動監控",
            font=("Arial", 12),
            command=self.start_system,
        )
        self.start_btn.pack(pady=10)

        self.log_area = scrolledtext.ScrolledText(
            root, width=60, height=15, state="disabled"
        )
        self.log_area.pack(pady=10)

        # 3. 啟動 Tkinter 的定時器，每 100 毫秒去檢查一次 Queue
        self.root.after(100, self.process_queue)

    def log(self, message):
        """將文字寫入 Queue，讓主執行緒去更新 UI"""
        self.msg_queue.put(message)

    def process_queue(self):
        """UI 主執行緒專用的更新函數"""
        while not self.msg_queue.empty():
            msg = self.msg_queue.get()
            self.log_area.config(state="normal")
            self.log_area.insert(tk.END, msg + "\n")
            self.log_area.see(tk.END)  # 自動捲動到最底下
            self.log_area.config(state="disabled")

        # 繼續設定下一次的 100 毫秒檢查
        self.root.after(100, self.process_queue)

    def start_system(self):
        """按鈕點擊事件：啟動背景 Thread"""
        self.start_btn.config(state=tk.DISABLED, text="監控運行中...")
        self.log("系統啟動，準備進入背景執行緒...")

        # 開啟獨立 Thread，設定 daemon=True 代表關閉視窗時背景也會強制結束
        bg_thread = threading.Thread(target=self.run_asyncio_loop, daemon=True)
        bg_thread.start()

    def run_asyncio_loop(self):
        """背景 Thread 的入口：在這裡建立全新的非同步事件迴圈"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 啟動我們真正的核心非同步主程式
        loop.run_until_complete(self.async_main_core())

    # ==========================================
    # 以下為 Asyncio 的世界，這裡面的扣絕對不會卡死 UI
    # ==========================================
    async def async_main_core(self):
        self.log("✅ Twitch WebSocket 監聽模組已啟動")

        # 模擬：等待 Twitch 推播開台通知
        await asyncio.sleep(2)
        self.log("🚨 收到 EventSub 通知：lanmeinotbeer 開台了！")

        # 模擬：動態開一個任務去跑 FFmpeg 影像比對
        asyncio.create_task(self.ffmpeg_monitor_task("lanmeinotbeer"))

        # 模擬：系統繼續監聽其他人的狀態
        while True:
            await asyncio.sleep(1)

    async def ffmpeg_monitor_task(self, broadcaster):
        self.log(f"🎥 開始持續擷取 {broadcaster} 的 HLS 串流畫面...")

        # 模擬影像比對的過程
        for i in range(3, 0, -1):
            self.log(f"[{broadcaster}] 畫面特徵分析中... ({i})")
            await asyncio.sleep(1.5)

        self.log(
            f"🚨🚨 [{broadcaster}] 畫面發生劇烈切換，正片開始！發送 Discord 通知！"
        )


if __name__ == "__main__":
    root = tk.Tk()
    app = StreamMonitorApp(root)
    root.mainloop()
