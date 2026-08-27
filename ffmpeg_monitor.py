"""開台後抽幀並判定正片。SIMULATE=1 時仍走舊的倒數示範。"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable

from PIL import Image, UnidentifiedImageError

from config import Settings
from image_hash import dhash_int
from standby import StandbyDetector, hashes_are_stable, load_reference_hashes
from tools import find_ffmpeg, find_streamlink_cli
from twitch_stream import StreamResolveError, resolve_twitch_stream

LogFn = Callable[[str], None]


async def simulate_ffmpeg_monitor(
    broadcaster: str,
    log: LogFn,
    stop_event: asyncio.Event,
) -> bool:
    """回傳 True 代表模擬判定「正片開始」。"""
    log(f"🎥 開始持續擷取 {broadcaster} 的 HLS 串流畫面...（模擬模式）")
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


async def monitor_broadcast(
    broadcaster: str,
    settings: Settings,
    log: LogFn,
    stop_event: asyncio.Event,
    *,
    already_live: bool,
) -> bool:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        log("❌ 找不到 ffmpeg。請安裝 FFmpeg 並加入 PATH，或把 ffmpeg.exe 放到程式同一個資料夾。")
        return False

    refs, used = load_reference_hashes(settings.standby_dir, broadcaster)
    if used:
        log(f"🖼️ [{broadcaster}] 待命參考圖：{', '.join(os.path.basename(p) for p in used)}")
    elif already_live:
        log(
            f"ℹ️ [{broadcaster}] 啟動時已在直播且沒有 standby/{broadcaster}.png，"
            "略過自動判定（避免把正片誤當成待命）。"
        )
        return False
    else:
        log(
            f"ℹ️ [{broadcaster}] 沒有待命參考圖，將在略過廣告後嘗試建立穩定 baseline。"
            f"建議放一張截圖到 standby/{broadcaster}.png"
        )

    tmp = tempfile.mkdtemp(prefix=f"standby-{broadcaster}-")
    frame_path = os.path.join(tmp, "frame.jpg")
    procs: list[asyncio.subprocess.Process] = []
    try:
        resolved = await _resolve_with_retry(broadcaster, log, stop_event)
        if resolved is None:
            return False
        log(f"🎥 [{broadcaster}] 取得 {resolved.quality} 串流，開始抽幀…")
        procs = await _start_grabbers(
            ffmpeg, broadcaster, resolved.url, frame_path, settings.frame_interval_sec, log
        )
        if not procs:
            return False
        return await _watch_frames(
            broadcaster,
            settings,
            log,
            stop_event,
            frame_path,
            procs,
            refs,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log(f"❌ [{broadcaster}] 畫面監控失敗：{exc}")
        return False
    finally:
        await _kill_all(procs)
        shutil.rmtree(tmp, ignore_errors=True)


async def _resolve_with_retry(
    login: str,
    log: LogFn,
    stop_event: asyncio.Event,
    attempts: int = 6,
):
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        if stop_event.is_set():
            return None
        try:
            return await asyncio.to_thread(resolve_twitch_stream, login)
        except StreamResolveError as exc:
            last_error = exc
            log(f"⏳ [{login}] 尚未取得 HLS（{attempt}/{attempts}）：{exc}")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=3)
                return None
            except asyncio.TimeoutError:
                pass
    raise StreamResolveError(str(last_error) if last_error else "無法解析串流")


async def _start_grabbers(
    ffmpeg: str,
    login: str,
    stream_url: str | None,
    frame_path: str,
    interval: float,
    log: LogFn,
) -> list[asyncio.subprocess.Process]:
    if stream_url:
        ff = await _spawn_ffmpeg(ffmpeg, ["-i", stream_url], frame_path, interval)
        return [ff]
    streamlink = find_streamlink_cli()
    if not streamlink:
        log(f"❌ [{login}] 解析結果沒有 URL，且找不到 streamlink 命令列可改走 pipe。")
        return []
    return await _spawn_pipe(ffmpeg, streamlink, login, frame_path, interval)


def _ffmpeg_output_args(frame_path: str, interval: float) -> list[str]:
    fps = 1.0 / max(interval, 1.0)
    return [
        "-an",
        "-vf",
        f"fps={fps}",
        "-q:v",
        "6",
        "-update",
        "1",
        "-y",
        frame_path,
    ]


def _creationflags() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return 0


async def _spawn_ffmpeg(
    ffmpeg: str,
    input_args: list[str],
    frame_path: str,
    interval: float,
) -> asyncio.subprocess.Process:
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "15",
        "-user_agent",
        "Mozilla/5.0",
        *input_args,
        *_ffmpeg_output_args(frame_path, interval),
    ]
    kwargs: dict = {
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.PIPE,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _creationflags()
    else:
        kwargs["start_new_session"] = True
    return await asyncio.create_subprocess_exec(*cmd, **kwargs)


async def _spawn_pipe(
    ffmpeg: str,
    streamlink: str,
    login: str,
    frame_path: str,
    interval: float,
) -> list[asyncio.subprocess.Process]:
    sl_cmd = [
        streamlink,
        "--stdout",
        "--twitch-disable-ads",
        f"https://www.twitch.tv/{login}",
        "480p,360p,160p,worst",
    ]
    flags = _creationflags()
    sl_kwargs: dict = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    ff_kwargs: dict = {
        "stdin": None,
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.PIPE,
    }
    if sys.platform == "win32":
        sl_kwargs["creationflags"] = flags
        ff_kwargs["creationflags"] = flags
    else:
        sl_kwargs["start_new_session"] = True
        ff_kwargs["start_new_session"] = True
    sl = await asyncio.create_subprocess_exec(*sl_cmd, **sl_kwargs)
    ff_cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        *_ffmpeg_output_args(frame_path, interval),
    ]
    ff_kwargs["stdin"] = sl.stdout
    ff = await asyncio.create_subprocess_exec(*ff_cmd, **ff_kwargs)
    if sl.stdout:
        sl.stdout.close()
    return [sl, ff]


async def _watch_frames(
    login: str,
    settings: Settings,
    log: LogFn,
    stop_event: asyncio.Event,
    frame_path: str,
    procs: list[asyncio.subprocess.Process],
    refs: list[int],
) -> bool:
    detector = StandbyDetector(refs, settings.hash_threshold, settings.confirm_frames)
    started = time.monotonic()
    last_sig: tuple[float, int] | None = None
    recent: list[int] = []
    ad_logged = False
    stable_logged = False
    standby_logs = 0
    stderr_tasks = [asyncio.create_task(_collect_stderr(proc)) for proc in procs]

    try:
        while not stop_event.is_set():
            dead = [proc for proc in procs if proc.returncode is not None]
            if dead:
                extras = []
                for task in stderr_tasks:
                    if task.done():
                        extras.extend(task.result()[-6:])
                detail = " | ".join(extras) if extras else f"exit={dead[0].returncode}"
                log(f"❌ [{login}] 抽幀行程結束：{detail}")
                return False

            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=max(settings.frame_interval_sec / 2, 0.5)
                )
                return False
            except asyncio.TimeoutError:
                pass

            if not os.path.isfile(frame_path) or os.path.getsize(frame_path) == 0:
                if time.monotonic() - started > 90:
                    log(f"❌ [{login}] 90 秒內沒抽到畫面")
                    return False
                continue

            sig = (os.path.getmtime(frame_path), os.path.getsize(frame_path))
            if sig == last_sig:
                continue
            last_sig = sig

            try:
                with Image.open(frame_path) as img:
                    img.load()
                    frame_hash = dhash_int(img)
            except (OSError, UnidentifiedImageError):
                continue

            elapsed = time.monotonic() - started
            if elapsed < settings.ad_skip_sec:
                if not ad_logged:
                    log(f"⏳ [{login}] 略過開台前 {settings.ad_skip_sec:.0f} 秒（廣告／過場）")
                    ad_logged = True
                continue

            if not detector.references:
                recent.append(frame_hash)
                recent = recent[-5:]
                if hashes_are_stable(recent, settings.hash_threshold):
                    detector.set_references(recent[-3:])
                    log(f"📌 [{login}] 已建立穩定待命 baseline")
                    stable_logged = True
                elif elapsed > 90 and not stable_logged:
                    log(
                        f"⚠️ [{login}] 畫面遲遲不穩定，可能已在正片或待命是動態影片。"
                        f"請改放 standby/{login}.png"
                    )
                    stable_logged = True
                continue

            state, dist = detector.observe(frame_hash)
            bits = 64
            if dist is None:
                continue
            if state == "standby":
                standby_logs += 1
                if standby_logs == 1 or standby_logs % 10 == 0:
                    log(f"[{login}] 與待命差 {dist}/{bits}（像待命）")
            elif state == "pending":
                log(
                    f"[{login}] 與待命差 {dist}/{bits}（不像 {detector.unlike_streak}/{settings.confirm_frames}）"
                )
            else:
                log(f"🚨🚨 [{login}] 畫面已離開待命（差 {dist}/{bits}），正片開始！")
                return True
        return False
    finally:
        for task in stderr_tasks:
            task.cancel()


async def _collect_stderr(proc: asyncio.subprocess.Process) -> list[str]:
    lines: list[str] = []
    if proc.stderr is None:
        return lines
    try:
        while True:
            raw = await proc.stderr.readline()
            if not raw:
                break
            text = raw.decode("utf-8", "replace").strip()
            if text:
                lines.append(text)
                if len(lines) > 40:
                    lines.pop(0)
    except Exception:
        pass
    return lines


async def _kill_all(procs: list[asyncio.subprocess.Process]) -> None:
    for proc in procs:
        if proc.returncode is not None:
            continue
        try:
            proc.kill()
        except ProcessLookupError:
            continue
    for proc in procs:
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
