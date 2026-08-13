import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resources.lib.token_cache import TokenCache


class TokenCacheTests(unittest.TestCase):
    def test_saves_and_reuses_unexpired_matching_token(self):
        with tempfile.TemporaryDirectory() as profile:
            cache = TokenCache(profile, clock=lambda: 1000)
            cache.save("fingerprint", "token", 2000)

            self.assertEqual("token", cache.load("fingerprint"))
            self.assertIsNone(cache.load("different"))
            mode = stat.S_IMODE(os.stat(os.path.join(profile, "access_token.json")).st_mode)
            self.assertEqual(0o600, mode)

    def test_rejects_token_inside_refresh_margin(self):
        with tempfile.TemporaryDirectory() as profile:
            path = os.path.join(profile, "access_token.json")
            with open(path, "w", encoding="utf-8") as destination:
                json.dump({"version": 1, "fingerprint": "fp", "token": "token", "expires_at": 1299}, destination)
            cache = TokenCache(profile, clock=lambda: 1000)

            self.assertIsNone(cache.load("fp"))

    def test_respects_larger_minimum_remaining_lifetime(self):
        with tempfile.TemporaryDirectory() as profile:
            cache = TokenCache(profile, clock=lambda: 1000)
            cache.save("fp", "token", 3000)

            self.assertEqual("token", cache.load("fp", minimum_remaining=1900))
            self.assertIsNone(cache.load("fp", minimum_remaining=2100))

    def test_rejects_non_finite_or_boolean_expiration(self):
        with tempfile.TemporaryDirectory() as profile:
            path = os.path.join(profile, "access_token.json")
            cache = TokenCache(profile, clock=lambda: 1000)
            for expires_at in (float("nan"), float("inf"), True, 10**1000):
                with self.subTest(expires_at=expires_at):
                    with open(path, "w", encoding="utf-8") as destination:
                        json.dump({
                            "version": 1,
                            "fingerprint": "fp",
                            "token": "token",
                            "expires_at": expires_at,
                        }, destination)
                    self.assertIsNone(cache.load("fp"))

    def test_save_works_without_fchmod(self):
        with tempfile.TemporaryDirectory() as profile:
            cache = TokenCache(profile, clock=lambda: 1000)
            with mock.patch("resources.lib.token_cache.os.fchmod", side_effect=AttributeError):
                cache.save("fp", "token", 2000)

            self.assertEqual("token", cache.load("fp"))

    def test_clear_removes_cache(self):
        with tempfile.TemporaryDirectory() as profile:
            cache = TokenCache(profile, clock=lambda: 1000)
            cache.save("fp", "token", 2000)
            cache.clear()

            self.assertFalse(os.path.exists(os.path.join(profile, "access_token.json")))


if __name__ == "__main__":
    unittest.main()
