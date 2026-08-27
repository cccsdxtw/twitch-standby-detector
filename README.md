# 實況守門員

Twitch 待命畫面 / 正片切換監控。版本 **v0.2.0**。

桌面程式：Tkinter 介面 + 背景 asyncio。會用 EventSub WebSocket 聽開台 / 下播；畫面比對與 FFmpeg 仍是模擬。正片判定後可打 Discord Webhook。

## 需求

- Python 3.10+
- Tkinter（Windows 的官方 Python 通常已附帶）
- 第三方套件見 `requirements.txt`

```bash
python -m pip install -r requirements.txt
```

## 設定

1. 複製 `.env.example` 為 `.env`（與 `app.py` 放在同一層；打包後則與 EXE 同層）。
2. 到 [Twitch Developer Console](https://dev.twitch.tv/console) 建立應用程式，Client ID 填進 `TWITCH_CLIENT_ID`。
   - OAuth Redirect URL 可填 `http://localhost`（註冊時必填）。
   - EventSub **WebSocket 只能用使用者 token**，不能用 app access token。第一次啟動會走 Device Code：開瀏覽器、輸入代碼。Token 存在 `twitch_token.json`，不要提交 Git。
3. `TWITCH_USER_LOGINS` 填要監的頻道（逗號分隔）。
4. 可選：`DISCORD_WEBHOOK_URL`。
5. 沒憑證要試 UI 時，設 `SIMULATE=1`。

WebSocket 訂閱有成本上限（約 10）。每個頻道若同時訂 `stream.online` 與 `stream.offline` 會佔 2，建議一次不要監太多台。

## 執行

```bash
python app.py
```

點「啟動自動監控」後：

1. 登入 Twitch（若尚未有有效 token）
2. 查目前是否已在直播（EventSub 只在「變成開台」時推播）
3. 訂閱 EventSub，開台則啟動畫面監控（目前模擬）、正片開始則通知 Discord

## 用萬用 PyInstaller 打包神器

專案已接打包神器慣例：

- 進入點是 `app.py`（含 `import tkinter`，會自動無黑框 `-w`）
- 圖示請用 `get_resource_path("app_master_icon.ico")`（沒有 ico 時會略過）
- 請把**整個專案資料夾**放在一起再拖 `app.py`，不要只拖單一檔

步驟：

1. 本機 `pip install pyinstaller` 與 `pip install -r requirements.txt`
2. 執行 [一鍵自動打包.bat](https://github.com/cccsdxtw/Universal-PyInstaller-Packager)
3. 拖曳 `app.py`；可選再拖一張正規 `.ico`
4. EXE 在 `打包成品_app`。把 `.env` 放到 **EXE 旁邊**（不要指望打包進秘密）

開發時若 `-w` 關掉黑框導致看不到崩潰訊息，可先用 `python app.py` 跑。

## 架構

- 主執行緒：Tkinter，每 100ms 從 Queue 更新 log
- 背景 daemon thread：獨立 asyncio loop
- `twitch_oauth.py`：Device Code + refresh
- `twitch_eventsub.py`：WebSocket、keepalive、reconnect
- `ffmpeg_monitor.py`：畫面比對（仍模擬）
- `paths.py`：`app_dir()` / `get_resource_path()` 給打包後路徑

## 還缺什麼

- 真 FFmpeg / HLS 抽幀與待命畫面判定
- 把 `ffmpeg.exe` 一併打包（打包神器不會自動抓外部執行檔）
