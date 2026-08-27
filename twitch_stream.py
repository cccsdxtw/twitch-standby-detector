"""用 Streamlink 向 Twitch 解析可播的 HLS。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SKIP_KEYS = {"best", "worst", "audio_only"}
HEIGHT_RE = re.compile(r"(\d+)p", re.IGNORECASE)


class StreamResolveError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedStream:
    quality: str
    url: str | None


def parse_height(quality: str) -> int | None:
    match = HEIGHT_RE.search(quality)
    if not match:
        return None
    return int(match.group(1))


def pick_stream(streams: dict[str, Any]) -> tuple[str, Any]:
    ranked: list[tuple[int, str, Any]] = []
    for key, stream in streams.items():
        if key.lower() in SKIP_KEYS or "audio" in key.lower():
            continue
        height = parse_height(key)
        ranked.append((height if height is not None else 9999, key, stream))
    ranked.sort(key=lambda item: item[0])
    under = [item for item in ranked if item[0] <= 480]
    if under:
        chosen = under[-1]
        return chosen[1], chosen[2]
    if ranked:
        chosen = ranked[0]
        return chosen[1], chosen[2]
    for key in ("worst", "best"):
        if key in streams:
            return key, streams[key]
    raise StreamResolveError("沒有可用的影像畫質")


def resolve_twitch_stream(login: str) -> ResolvedStream:
    from streamlink import Streamlink

    session = Streamlink()
    session.set_option("hls-live-edge", 2)
    url = f"https://www.twitch.tv/{login}"
    streams = None
    last_error: Exception | None = None
    try:
        _, plugin_class, resolved_url = session.resolve_url(url)
        try:
            plugin = plugin_class(session, resolved_url, options={"disable-ads": True})
        except TypeError:
            plugin = plugin_class(session, resolved_url)
            setter = getattr(getattr(plugin, "options", None), "set", None)
            if callable(setter):
                setter("disable-ads", True)
        streams = plugin.streams()
    except Exception as exc:
        last_error = exc
        try:
            streams = session.streams(url)
        except Exception as exc2:
            raise StreamResolveError(str(exc2)) from exc2

    if not streams:
        raise StreamResolveError(str(last_error) if last_error else "找不到直播流（可能尚未真正開播）")

    quality, stream = pick_stream(streams)
    stream_url = getattr(stream, "url", None)
    if stream_url:
        stream_url = str(stream_url)
    return ResolvedStream(quality=quality, url=stream_url)
