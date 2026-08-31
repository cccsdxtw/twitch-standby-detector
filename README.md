# 實況守門員

Twitch 待命畫面 / 正片切換監控。版本 **v0.11.0**。版本號避開 4。

桌面程式：Tkinter + 背景 asyncio。EventSub 聽開台，streamlink + FFmpeg 抽幀，dHash 比對待命畫面，正片開始後打 Discord Webhook。

## 需求

- Python 3.10+
- Tkinter
- [FFmpeg](https://ffmpeg.org/download.html)（`PATH` 或與 `app.py` / EXE 放同一層，Windows 檔名 `ffmpeg.exe`）
- `pip install -r requirements.txt`

## 設定

1. 打開程式後按 **「設定 ID / 網址」**，填 Twitch Client ID 與 Discord Webhook（存在本機 `.env`）。
2. [Twitch Developer Console](https://dev.twitch.tv/console) 建立應用。Redirect URL 可填 `http://localhost`。第一次啟動會 Device Code 登入，token 存 `twitch_token.json`。
3. 視窗裡新增頻道。名稱前會顯示 Twitch 頭像；列太長可左右拖。每台可開關通知、分開勾「開網頁／關網頁」、調「像待命」相似度（預設 60%）、選略過色（標題等會變的區塊），並選待命圖片或影片。視窗會依勾選算出 EventSub 預算（10）：只聽開台每台 1，再勾關網頁 +1。
5. **強烈建議**把待命截圖放到 `standby/<登入名>.png`（例如 `standby/lanmeinotbeer.png`）。沒有參考圖時：剛開台會試著抓穩定 baseline；**啟動時已經在直播則略過判定**。
6. 沒憑證試 UI：`SIMULATE=1`。

可調：`FRAME_INTERVAL_SEC`（預設 3）、`AD_SKIP_SEC`（20）、`CONFIRM_FRAMES`（連續 4 張不像待命才通知）。**開頭偵測時限**在設定裡填分鐘數，預設 60（開台滿 1 小時就不再抽幀判定正片；`SKIP_START_AFTER_MIN`，0＝不略過）。每台的相似度與略過色存在 `watchlist.json`。

EventSub 預設只訂 `stream.online`（每台成本 1）。有勾關網頁的台才加 `stream.offline`。總預算 10，名單依序能塞多少聽多少。不做 Helix 輪詢。

## 執行

```bash
python app.py
```

流程：登入 Twitch → 補查已在直播 → EventSub 開台 → 開台抽幀 → 離開待命 → Discord。

## 用萬用 PyInstaller 打包神器

邏輯都在 **`app.py` 一個檔**，直接拖進去即可（有 `import tkinter` → `-w`）。

- 圖示：`get_resource_path("app_master_icon.ico")`
- **FFmpeg 不會被打進 EXE**。把 `ffmpeg.exe` 放到 EXE 旁邊。
- streamlink 資源較多；若 EXE 解析不到台，請改打包指令加上 `--collect-all streamlink`（萬用 bat 預設不會加）。
- `.env`、`standby\` 截圖放 EXE 旁邊。
