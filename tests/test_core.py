from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from config import parse_logins
from discord_notify import build_webhook_body
from paths import app_dir, get_resource_path
from twitch_eventsub import event_from_notification, parse_ws_message
from twitch_oauth import TokenPair, token_from_response


class ParseLoginsTests(unittest.TestCase):
    def test_comma_and_dedupe(self) -> None:
        self.assertEqual(
            parse_logins(" LanMeiNotBeer, lanmeinotbeer; other "),
            ("lanmeinotbeer", "other"),
        )

    def test_empty(self) -> None:
        self.assertEqual(parse_logins("  ,  "), ())


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
        from twitch_oauth import load_token, save_token, token_path

        pair = TokenPair(access_token="tok", refresh_token="ref", expires_at=9999999999)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("twitch_oauth.app_dir", return_value=tmp):
                save_token(pair)
                self.assertTrue(os.path.isfile(token_path()))
                loaded = load_token()
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.access_token, "tok")
        self.assertEqual(loaded.refresh_token, "ref")


if __name__ == "__main__":
    unittest.main()
