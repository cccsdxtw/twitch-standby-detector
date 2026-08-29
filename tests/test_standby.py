from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace

from PIL import Image, ImageDraw

from app import (
    StandbyDetector,
    describe_references,
    dhash_int,
    hamming_distance,
    hashes_are_stable,
    import_standby_image,
    list_reference_files,
    load_reference_hashes,
    parse_height,
    pick_stream,
)


def _scene(kind: str) -> Image.Image:
    img = Image.new("RGB", (128, 128), "black")
    draw = ImageDraw.Draw(img)
    if kind == "standby":
        draw.rectangle([8, 48, 120, 80], fill="white")
        draw.ellipse([48, 48, 80, 80], fill="red")
    else:
        draw.rectangle([0, 0, 127, 127], fill="green")
        draw.polygon([(8, 120), (64, 8), (120, 120)], fill="yellow")
    return img


class HashTests(unittest.TestCase):
    def test_identical_images_zero_distance(self) -> None:
        a = dhash_int(_scene("standby"))
        b = dhash_int(_scene("standby"))
        self.assertEqual(hamming_distance(a, b), 0)

    def test_different_scenes_far_apart(self) -> None:
        dist = hamming_distance(dhash_int(_scene("standby")), dhash_int(_scene("content")))
        self.assertGreater(dist, 10)


class DetectorTests(unittest.TestCase):
    def test_requires_confirm_streak(self) -> None:
        standby = dhash_int(_scene("standby"))
        content = dhash_int(_scene("content"))
        detector = StandbyDetector([standby], threshold=8, confirm_frames=3)
        self.assertEqual(detector.observe(standby)[0], "standby")
        self.assertEqual(detector.observe(content)[0], "pending")
        self.assertEqual(detector.observe(content)[0], "pending")
        self.assertEqual(detector.observe(content)[0], "content")

    def test_standby_resets_streak(self) -> None:
        standby = dhash_int(_scene("standby"))
        content = dhash_int(_scene("content"))
        detector = StandbyDetector([standby], threshold=8, confirm_frames=3)
        detector.observe(content)
        detector.observe(content)
        self.assertEqual(detector.observe(standby)[0], "standby")
        self.assertEqual(detector.unlike_streak, 0)

    def test_stable_window(self) -> None:
        h = dhash_int(_scene("standby"))
        self.assertTrue(hashes_are_stable([h, h, h], threshold=4))
        other = dhash_int(_scene("content"))
        self.assertFalse(hashes_are_stable([h, h, other], threshold=4))


class ReferenceFilesTests(unittest.TestCase):
    def test_matches_login_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "lanmeinotbeer.png"), "wb").close()
            open(os.path.join(tmp, "lanmeinotbeer-2.jpg"), "wb").close()
            open(os.path.join(tmp, "someoneelse.png"), "wb").close()
            found = list_reference_files(tmp, "LanMeiNotBeer")
            names = {os.path.basename(p) for p in found}
            self.assertEqual(names, {"lanmeinotbeer.png", "lanmeinotbeer-2.jpg"})

    def test_import_image_saves_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.jpg")
            Image.new("RGB", (32, 32), "blue").save(src)
            dest_dir = os.path.join(tmp, "standby")
            files = import_standby_image(dest_dir, "Foo", src)
            self.assertTrue(files[0].endswith("foo.png"))
            self.assertEqual(describe_references(dest_dir, "foo"), "foo.png")

    def test_load_skips_empty_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Image.new("RGB", (32, 32), "red").save(os.path.join(tmp, "foo.png"))
            open(os.path.join(tmp, "foo-bad.png"), "wb").close()
            hashes, used = load_reference_hashes(tmp, "foo")
            self.assertEqual(len(hashes), 1)
            self.assertTrue(used[0].endswith("foo.png"))


class IgnoreColorTests(unittest.TestCase):
    def _card(self, title: tuple[int, int, int]) -> Image.Image:
        img = Image.new("RGB", (128, 128), (12, 16, 40))
        draw = ImageDraw.Draw(img)
        draw.rectangle([6, 6, 122, 28], fill=title)
        draw.ellipse([44, 52, 84, 92], fill="red")
        return img

    def test_ignore_title_color_keeps_same_layout_close(self) -> None:
        a = self._card((255, 255, 255))
        b = self._card((230, 230, 210))
        raw = hamming_distance(dhash_int(a), dhash_int(b))
        masked = hamming_distance(
            dhash_int(a, ignore_color=(255, 255, 255), ignore_tolerance=50),
            dhash_int(b, ignore_color=(255, 255, 255), ignore_tolerance=50),
        )
        self.assertLessEqual(masked, raw)
        self.assertLessEqual(masked, 6)


class PickStreamTests(unittest.TestCase):
    def test_parse_height(self) -> None:
        self.assertEqual(parse_height("480p60"), 480)
        self.assertIsNone(parse_height("best"))

    def test_prefers_highest_at_or_below_480(self) -> None:
        streams = {
            "160p": SimpleNamespace(url="160"),
            "360p": SimpleNamespace(url="360"),
            "480p": SimpleNamespace(url="480"),
            "720p60": SimpleNamespace(url="720"),
            "best": SimpleNamespace(url="best"),
            "audio_only": SimpleNamespace(url="audio"),
        }
        quality, stream = pick_stream(streams)
        self.assertEqual(quality, "480p")
        self.assertEqual(stream.url, "480")


if __name__ == "__main__":
    unittest.main()
