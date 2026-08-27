# 實況守門員

Twitch 待命畫面 / 正片切換監控。版本 **v0.1.0**。

目前是桌面骨架：Tkinter 介面 + 背景 asyncio 迴圈，用 Queue 把背景 log 安全送回 UI。EventSub、FFmpeg 與 Discord 仍是模擬流程。

## 需求

- Python 3.10+
- Tkinter（多數系統隨 Python 附帶；Linux 可能需另外安裝 `python3-tk`）

無需第三方套件。

## 執行

```bash
python3 app.py
```

點「啟動自動監控」後，背景執行緒會模擬：

1. Twitch EventSub 開台通知
2. FFmpeg / HLS 畫面特徵分析
3. 偵測到劇烈切換時發出 Discord 通知（目前僅寫入 log）

## 架構（v0.1.0）

- 主執行緒：Tkinter `mainloop`，每 100ms 從 `queue.Queue` 讀訊息並更新 log
- 背景 daemon thread：獨立 `asyncio` event loop，避免卡住 UI
- `async_main_core`：模擬持續監聽
- `ffmpeg_monitor_task`：模擬單一實況主的畫面比對

## 授權

尚未指定。
