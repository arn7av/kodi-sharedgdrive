import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resources.lib.folder_cache import FolderResultCache


class FolderResultCacheTests(unittest.TestCase):
    def test_reuses_unexpired_matching_folder(self):
        with tempfile.TemporaryDirectory() as profile:
            cache = FolderResultCache(profile, clock=lambda: 1000)
            items = [{"id": "video_1", "driveId": "drive_1"}]
            cache.put("fingerprint", "folder_1", items)

            self.assertEqual(items, cache.get("fingerprint", "folder_1"))
            self.assertIsNone(cache.get("different", "folder_1"))

    def test_expires_after_three_minutes(self):
        now = [1000]
        with tempfile.TemporaryDirectory() as profile:
            cache = FolderResultCache(profile, clock=lambda: now[0])
            cache.put("fingerprint", "folder_1", [{"id": "video_1"}])
            now[0] += 181

            self.assertIsNone(cache.get("fingerprint", "folder_1"))

    def test_caps_cache_at_fifty_most_recent_entries(self):
        now = [1000]
        with tempfile.TemporaryDirectory() as profile:
            cache = FolderResultCache(profile, clock=lambda: now[0])
            for index in range(55):
                now[0] += 1
                cache.put("fingerprint", "folder_{0}".format(index), [{"id": "video_{0}".format(index)}])

            with open(os.path.join(profile, "folder_results.json"), "r", encoding="utf-8") as source:
                document = json.load(source)
            self.assertEqual(50, len(document["entries"]))
            self.assertNotIn("folder_0", document["entries"])
            self.assertIn("folder_54", document["entries"])

    def test_rejects_single_entry_larger_than_file_bound(self):
        with tempfile.TemporaryDirectory() as profile:
            cache = FolderResultCache(profile, clock=lambda: 1000)
            cache.put("fingerprint", "folder_1", [{"id": "video_1", "name": "x" * (2 * 1024 * 1024)}])

            self.assertIsNone(cache.get("fingerprint", "folder_1"))

    def test_rejects_non_finite_or_boolean_timestamps(self):
        with tempfile.TemporaryDirectory() as profile:
            path = os.path.join(profile, "folder_results.json")
            cache = FolderResultCache(profile, clock=lambda: 1000)
            for cached_at in (float("nan"), float("inf"), True, 10**1000):
                with self.subTest(cached_at=cached_at):
                    with open(path, "w", encoding="utf-8") as destination:
                        json.dump({
                            "version": 1,
                            "fingerprint": "fp",
                            "entries": {
                                "folder_1": {"cached_at": cached_at, "items": []},
                            },
                        }, destination)
                    self.assertIsNone(cache.get("fp", "folder_1"))

    def test_put_works_without_fchmod(self):
        with tempfile.TemporaryDirectory() as profile:
            cache = FolderResultCache(profile, clock=lambda: 1000)
            with mock.patch("resources.lib.folder_cache.os.fchmod", side_effect=AttributeError):
                cache.put("fp", "folder_1", [])

            self.assertEqual([], cache.get("fp", "folder_1"))

    def test_clear_removes_cache(self):
        with tempfile.TemporaryDirectory() as profile:
            cache = FolderResultCache(profile, clock=lambda: 1000)
            cache.put("fingerprint", "folder_1", [])
            cache.clear()

            self.assertFalse(os.path.exists(os.path.join(profile, "folder_results.json")))


if __name__ == "__main__":
    unittest.main()
