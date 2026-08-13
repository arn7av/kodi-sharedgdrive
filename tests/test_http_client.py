import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resources.lib.http_client import HttpClient


class HttpClientTests(unittest.TestCase):
    def test_allows_expected_google_hosts(self):
        HttpClient._validate_url("https://oauth2.googleapis.com/token")
        HttpClient._validate_url("https://www.googleapis.com/drive/v3/files")

    def test_rejects_non_https_or_non_allowlisted_hosts(self):
        invalid_urls = (
            "http://www.googleapis.com/drive/v3/files",
            "https://evil.example/files",
            "https://www.googleapis.com.evil.example/files",
            "https://user:password@www.googleapis.com/files",
            "https://www.googleapis.com:444/files",
        )
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                HttpClient._validate_url(url)


if __name__ == "__main__":
    unittest.main()
