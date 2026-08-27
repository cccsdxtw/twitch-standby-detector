# 實況守門員

Twitch 待命畫面 / 正片切換監控。版本 **v0.3.0**。

桌面程式：Tkinter + 背景 asyncio。EventSub 聽開台／下播，streamlink + FFmpeg 抽幀，dHash 比對待命畫面，正片開始後打 Discord Webhook。

## 需求

- Python 3.10+
- Tkinter
- [FFmpeg](https://ffmpeg.org/download.html)（`PATH` 或與 `app.py` / EXE 放同一層，Windows 檔名 `ffmpeg.exe`）
- `pip install -r requirements.txt`

## 設定

1. 複製 `.env.example` 為 `.env`（與 `app.py` 同層；打包後與 EXE 同層）。
2. [Twitch Developer Console](https://dev.twitch.tv/console) 建立應用，填 `TWITCH_CLIENT_ID`。Redirect URL 可填 `http://localhost`。第一次啟動會 Device Code 登入，token 存 `twitch_token.json`。
3. `TWITCH_USER_LOGINS` 填頻道（逗號分隔）。
4. 可選 `DISCORD_WEBHOOK_URL`。
5. **強烈建議**把待命截圖放到 `standby/<登入名>.png`（例如 `standby/lanmeinotbeer.png`）。沒有參考圖時：剛開台會試著抓穩定 baseline；**啟動時已經在直播則略過判定**。
6. 沒憑證試 UI：`SIMULATE=1`。

可調：`FRAME_INTERVAL_SEC`（預設 3）、`AD_SKIP_SEC`（20）、`CONFIRM_FRAMES`（連續 4 張不像待命才通知）、`HASH_THRESHOLD`（dHash 距離，預設 16）。

WebSocket 訂閱成本上限約 10；online+offline 每台佔 2。

## 執行

```bash
python app.py
```

流程：登入 Twitch → 補查已在直播 → EventSub → 開台抽幀 → 離開待命 → Discord。

## 用萬用 PyInstaller 打包神器

- 拖整個專案裡的 `app.py`（含 `import tkinter` → `-w`）
- 圖示：`get_resource_path("app_master_icon.ico")`
- **FFmpeg 不會被打進 EXE**。把 `ffmpeg.exe` 放到 EXE 旁邊。
- streamlink 資源較多；若 EXE 解析不到台，請改打包指令加上 `--collect-all streamlink`（萬用 bat 預設不會加）。
- `.env`、`standby\` 截圖放 EXE 旁邊。

## 架構

- `twitch_eventsub.py`：WebSocket
- `twitch_stream.py`：streamlink 解析 HLS
- `ffmpeg_monitor.py`：FFmpeg 抽幀
- `standby.py` / `image_hash.py`：dHash 判定（不依賴 OpenCV）
- `paths.py`：打包後路徑
