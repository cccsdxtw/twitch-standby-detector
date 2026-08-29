from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from app import (
    ChannelPref,
    TokenPair,
    app_dir,
    build_live_message,
    build_start_message,
    build_webhook_body,
    clamp_similarity_pct,
    event_from_notification,
    get_resource_path,
    load_channel_prefs,
    parse_logins,
    parse_ws_message,
    save_channel_prefs,
    similarity_pct_to_threshold,
    token_from_response,
    upsert_env_values,
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
                            display_name="貓辣妹",
                            similarity_pct=70,
                        ),
                        ChannelPref("lanmeinotbeer", notify_live=False, notify_start=True),
                    ]
                )
                prefs = load_channel_prefs()
        self.assertEqual(prefs[0].login, "maoramei")
        self.assertEqual(prefs[0].display_name, "貓辣妹")
        self.assertTrue(prefs[0].notify_live)
        self.assertFalse(prefs[0].notify_start)
        self.assertFalse(prefs[1].notify_live)
        self.assertTrue(prefs[1].notify_start)
        self.assertEqual(prefs[1].display_name, "")
        self.assertEqual(prefs[0].similarity_pct, 70)
        self.assertEqual(prefs[1].similarity_pct, 60)


class SimilarityTests(unittest.TestCase):
    def test_default_sixty_percent(self) -> None:
        self.assertEqual(similarity_pct_to_threshold(60), 25)

    def test_clamp(self) -> None:
        self.assertEqual(clamp_similarity_pct("0"), 1)
        self.assertEqual(clamp_similarity_pct("200"), 99)
        self.assertEqual(clamp_similarity_pct("x"), 60)


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
