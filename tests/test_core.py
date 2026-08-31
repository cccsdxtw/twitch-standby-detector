from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from PIL import Image

from app import (
    AVATAR_SIZE,
    ChannelPref,
    TokenPair,
    app_dir,
    avatar_cache_path,
    build_live_message,
    build_start_message,
    build_webhook_body,
    clamp_similarity_pct,
    event_from_notification,
    get_resource_path,
    load_channel_prefs,
    parse_helix_time,
    parse_ignore_color,
    should_skip_start_detect,
    clamp_skip_start_after_min,
    skip_start_hms,
    skip_start_after_label,
    parse_logins,
    plan_eventsub,
    eventsub_cost,
    describe_eventsub_plan,
    parse_ws_message,
    prepare_avatar_image,
    save_channel_prefs,
    twitch_channel_url,
    cdp_close_tab_url,
    cdp_new_tab_url,
    parse_cdp_target_id,
    similarity_pct_to_threshold,
    token_from_response,
    twitch_user_from_helix,
    upsert_env_values,
    write_avatar_bytes,
)


class ParseLoginsTests(unittest.TestCase):
    def test_comma_and_dedupe(self) -> None:
        self.assertEqual(
            parse_logins(" LanMeiNotBeer, lanmeinotbeer; other "),
            ("lanmeinotbeer", "other"),
        )

    def test_empty(self) -> None:
        self.assertEqual(parse_logins("  ,  "), ())

    def test_newlines_and_urls(self) -> None:
        self.assertEqual(
            parse_logins("https://www.twitch.tv/Foo\nbar, baz"),
            ("foo", "bar", "baz"),
        )


class EnvUpsertTests(unittest.TestCase):
    def test_updates_and_keeps_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# keep\nTWITCH_CLIENT_ID=old\nSIMULATE=0\n")
            with mock.patch("app.app_dir", return_value=tmp):
                upsert_env_values(
                    {
                        "TWITCH_CLIENT_ID": "newid",
                        "DISCORD_WEBHOOK_URL": "https://example.com/hook",
                    }
                )
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        self.assertIn("# keep", text)
        self.assertIn("TWITCH_CLIENT_ID=newid", text)
        self.assertIn("SIMULATE=0", text)
        self.assertIn("DISCORD_WEBHOOK_URL=https://example.com/hook", text)


class PathsTests(unittest.TestCase):
    def test_resource_path_unfrozen(self) -> None:
        path = get_resource_path("app_master_icon.ico")
        self.assertTrue(path.endswith("app_master_icon.ico"))
        self.assertTrue(os.path.isabs(path))

    def test_app_dir_unfrozen(self) -> None:
        self.assertTrue(os.path.isdir(app_dir()))


class EventSubParseTests(unittest.TestCase):
    def test_welcome(self) -> None:
        raw = json.dumps(
            {
                "metadata": {"message_type": "session_welcome"},
                "payload": {"session": {"id": "abc", "keepalive_timeout_seconds": 10}},
            }
        )
        msg_type, data = parse_ws_message(raw)
        self.assertEqual(msg_type, "session_welcome")
        self.assertEqual(data["payload"]["session"]["id"], "abc")

    def test_notification_online(self) -> None:
        raw = {
            "metadata": {"message_type": "notification"},
            "payload": {
                "subscription": {"type": "stream.online"},
                "event": {
                    "broadcaster_user_login": "lanmeinotbeer",
                    "broadcaster_user_name": "Lan",
                },
            },
        }
        msg_type, data = parse_ws_message(json.dumps(raw))
        self.assertEqual(msg_type, "notification")
        event_type, event = event_from_notification(data)
        self.assertEqual(event_type, "stream.online")
        self.assertEqual(event["broadcaster_user_login"], "lanmeinotbeer")

    def test_notification_offline(self) -> None:
        raw = {
            "metadata": {"message_type": "notification"},
            "payload": {
                "subscription": {"type": "stream.offline"},
                "event": {"broadcaster_user_login": "maoramei"},
            },
        }
        _msg_type, data = parse_ws_message(json.dumps(raw))
        event_type, event = event_from_notification(data)
        self.assertEqual(event_type, "stream.offline")
        self.assertEqual(event["broadcaster_user_login"], "maoramei")


class DiscordTests(unittest.TestCase):
    def test_truncates_content(self) -> None:
        body = build_webhook_body("x" * 3000)
        self.assertEqual(len(body["content"]), 2000)

    def test_live_message_format(self) -> None:
        self.assertEqual(
            build_live_message("貓辣妹", "maoramei"),
            "「貓辣妹」在實況了\n來去 https://www.twitch.tv/maoramei 看看",
        )

    def test_start_message_format(self) -> None:
        self.assertEqual(
            build_start_message("貓辣妹", "maoramei"),
            "「貓辣妹」正片開始了\n來去 https://www.twitch.tv/maoramei 看看",
        )


class WatchlistPrefTests(unittest.TestCase):
    def test_roundtrip_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("app.app_dir", return_value=tmp):
                save_channel_prefs(
                    [
                        ChannelPref(
                            "maoramei",
                            notify_live=True,
                            notify_start=False,
                            open_watch=True,
                            close_watch=True,
                            display_name="貓辣妹",
                            similarity_pct=70,
                            ignore_color="#ffffff",
                            ignore_tolerance=35,
                        ),
                        ChannelPref("lanmeinotbeer", notify_live=False, notify_start=True),
                    ]
                )
                prefs = load_channel_prefs()
        self.assertEqual(prefs[0].login, "maoramei")
        self.assertEqual(prefs[0].display_name, "貓辣妹")
        self.assertTrue(prefs[0].notify_live)
        self.assertFalse(prefs[0].notify_start)
        self.assertTrue(prefs[0].open_watch)
        self.assertTrue(prefs[0].close_watch)
        self.assertFalse(prefs[1].open_watch)
        self.assertFalse(prefs[1].close_watch)
        self.assertFalse(prefs[1].notify_live)
        self.assertTrue(prefs[1].notify_start)
        self.assertFalse(prefs[1].open_watch)
        self.assertEqual(prefs[1].display_name, "")
        self.assertEqual(prefs[0].similarity_pct, 70)
        self.assertEqual(prefs[0].ignore_color, "#ffffff")
        self.assertEqual(prefs[0].ignore_tolerance, 35)
        self.assertEqual(prefs[1].similarity_pct, 60)

    def test_open_watch_defaults_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "watchlist.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('[{"login": "maoramei", "notify_live": true}]\n')
            with mock.patch("app.app_dir", return_value=tmp):
                prefs = load_channel_prefs()
        self.assertFalse(prefs[0].open_watch)

    def test_channel_url(self) -> None:
        self.assertEqual(twitch_channel_url("MaoRamei"), "https://www.twitch.tv/maoramei")
        self.assertEqual(twitch_channel_url(""), "")

    def test_eventsub_plan_mixes_open_and_close(self) -> None:
        prefs = [
            ChannelPref("a", close_watch=True),
            ChannelPref("b", close_watch=True),
            ChannelPref("c"),
            ChannelPref("d"),
            ChannelPref("e", close_watch=True),
            ChannelPref("f"),
            ChannelPref("g"),
            ChannelPref("h"),
        ]
        plan = plan_eventsub(prefs)
        # 2+2+1+1+2+1+1 = 10, h needs 1 more → skip
        self.assertEqual(plan.included, ("a", "b", "c", "d", "e", "f", "g"))
        self.assertEqual(plan.skipped, ("h",))
        self.assertEqual(plan.cost, 10)
        self.assertEqual(eventsub_cost(ChannelPref("x")), 1)
        self.assertEqual(eventsub_cost(ChannelPref("x", close_watch=True)), 2)
        self.assertIn("可聽 7 台", describe_eventsub_plan(plan))
        self.assertIn("10/10", describe_eventsub_plan(plan))

    def test_eventsub_plan_ten_online_only(self) -> None:
        prefs = [ChannelPref(f"u{i}") for i in range(12)]
        plan = plan_eventsub(prefs)
        self.assertEqual(len(plan.included), 10)
        self.assertEqual(plan.skipped, ("u10", "u11"))
        self.assertEqual(plan.cost, 10)

    def test_cdp_urls_and_target_id(self) -> None:
        self.assertIn(
            "json/new?https%3A%2F%2Fwww.twitch.tv%2Fmaoramei",
            cdp_new_tab_url("https://www.twitch.tv/maoramei"),
        )
        self.assertIn("json/close/abc-1", cdp_close_tab_url("abc-1"))
        self.assertEqual(parse_cdp_target_id({"id": "tab-9"}), "tab-9")
        self.assertEqual(parse_cdp_target_id({}), "")


class SkipStartDetectTests(unittest.TestCase):
    def test_parse_helix_zulu(self) -> None:
        parsed = parse_helix_time("2026-08-30T12:00:00Z")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.hour, 12)

    def test_skip_after_one_hour_default(self) -> None:
        started = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        now = started + timedelta(minutes=59)
        self.assertFalse(should_skip_start_detect(started, 60, now=now))
        now = started + timedelta(hours=1)
        self.assertTrue(should_skip_start_detect(started, 60, now=now))

    def test_zero_means_never_skip(self) -> None:
        started = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        now = started + timedelta(hours=8)
        self.assertFalse(should_skip_start_detect(started, 0, now=now))

    def test_unknown_start_not_skipped(self) -> None:
        self.assertFalse(should_skip_start_detect(None, 60))

    def test_clamp_and_label(self) -> None:
        self.assertEqual(clamp_skip_start_after_min("60"), 60)
        self.assertEqual(clamp_skip_start_after_min("-3"), 60)
        self.assertEqual(clamp_skip_start_after_min("x"), 60)
        self.assertEqual(skip_start_after_label(60), "1 小時")
        self.assertEqual(skip_start_after_label(46), "46 分鐘")
        self.assertEqual(skip_start_after_label(90), "1 小時 30 分鐘")
        self.assertEqual(skip_start_after_label(0), "不略過")
        self.assertEqual(skip_start_hms(90), (1, 30))
        self.assertEqual(skip_start_hms(46), (0, 46))


class SimilarityTests(unittest.TestCase):
    def test_default_sixty_percent(self) -> None:
        self.assertEqual(similarity_pct_to_threshold(60), 25)

    def test_clamp(self) -> None:
        self.assertEqual(clamp_similarity_pct("0"), 1)
        self.assertEqual(clamp_similarity_pct("200"), 99)
        self.assertEqual(clamp_similarity_pct("x"), 60)

    def test_parse_color(self) -> None:
        self.assertEqual(parse_ignore_color("#ff00aa"), (255, 0, 170))
        self.assertIsNone(parse_ignore_color(""))


class AvatarTests(unittest.TestCase):
    def test_helix_user_includes_profile_image(self) -> None:
        user = twitch_user_from_helix(
            {
                "id": "1",
                "login": "MaoRamei",
                "display_name": "貓辣妹",
                "profile_image_url": "https://static-cdn.jtvnw.net/x.png",
            }
        )
        self.assertIsNotNone(user)
        assert user is not None
        self.assertEqual(user.login, "maoramei")
        self.assertEqual(user.display_name, "貓辣妹")
        self.assertEqual(user.profile_image_url, "https://static-cdn.jtvnw.net/x.png")

    def test_helix_user_rejects_incomplete(self) -> None:
        self.assertIsNone(twitch_user_from_helix({"login": "x", "display_name": "X"}))

    def test_prepare_avatar_is_round_png_size(self) -> None:
        src = Image.new("RGB", (300, 200), "red")
        out = prepare_avatar_image(src, 24)
        self.assertEqual(out.size, (24, 24))
        self.assertEqual(out.mode, "RGBA")
        self.assertEqual(out.getpixel((12, 12))[:3], (255, 0, 0))
        self.assertEqual(out.getpixel((0, 0))[3], 0)

    def test_write_and_cache_path(self) -> None:
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), "blue").save(buf, "PNG")
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "face.png")
            write_avatar_bytes(buf.getvalue(), dest)
            self.assertTrue(os.path.isfile(dest))
            with Image.open(dest) as img:
                self.assertEqual(img.size, (AVATAR_SIZE, AVATAR_SIZE))
            with mock.patch("app.app_dir", return_value=tmp):
                path = avatar_cache_path("MaoRamei")
            self.assertEqual(os.path.basename(path), "maoramei.png")
            self.assertTrue(os.path.isdir(os.path.join(tmp, "avatar_cache")))


class TokenTests(unittest.TestCase):
    def test_from_response_sets_expiry(self) -> None:
        pair = token_from_response(
            {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
        )
        self.assertEqual(pair.access_token, "a")
        self.assertTrue(pair.access_valid())

    def test_expired_token_not_valid(self) -> None:
        pair = TokenPair(access_token="a", refresh_token="r", expires_at=0)
        self.assertFalse(pair.access_valid())

    def test_save_and_load_roundtrip(self) -> None:
        from app import load_token, save_token, token_path

        pair = TokenPair(access_token="tok", refresh_token="ref", expires_at=9999999999)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("app.app_dir", return_value=tmp):
                save_token(pair)
                self.assertTrue(os.path.isfile(token_path()))
                loaded = load_token()
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.access_token, "tok")
        self.assertEqual(loaded.refresh_token, "ref")


if __name__ == "__main__":
    unittest.main()
