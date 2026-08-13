import io
import json
import pathlib
from email.message import Message
import sys
import unittest
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resources.lib import http_client
from resources.lib.errors import DriveError
from resources.lib.http_client import HttpClient


def _http_403(document=None, raw_body=None):
    if raw_body is None:
        raw_body = b"" if document is None else json.dumps(document).encode("utf-8")
    return urllib.error.HTTPError(
        "https://www.googleapis.com/drive/v3/files/abc",
        403,
        "Forbidden",
        Message(),
        io.BytesIO(raw_body),
    )


class _RaisingOpener:
    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def open(self, request, timeout=None):
        self.calls += 1
        raise self._exc


class _ProbeResponse:
    def __init__(self, status):
        self.status = status
        self.read_calls = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        return False

    def getcode(self):
        return self.status

    def read(self, size=-1):
        self.read_calls += 1
        raise AssertionError("media preflight must not read the response body")


class _RecordingOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        return self.response


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

    def test_media_probe_sends_one_byte_range_and_does_not_read_body(self):
        for status in (200, 206):
            with self.subTest(status=status):
                response = _ProbeResponse(status)
                opener = _RecordingOpener(response)
                client = HttpClient(attempts=1, opener=opener)

                client.probe_media(
                    "https://www.googleapis.com/drive/v3/files/abc?alt=media",
                    headers={"Authorization": "Bearer token"},
                )

                self.assertEqual("bytes=0-0", opener.requests[0].get_header("Range"))
                self.assertEqual("Bearer token", opener.requests[0].get_header("Authorization"))
                self.assertEqual(0, response.read_calls)
                self.assertTrue(response.closed)

    def test_media_probe_treats_google_redirect_as_inconclusive_success(self):
        redirect = urllib.error.HTTPError(
            "https://www.googleapis.com/drive/v3/files/abc?alt=media",
            302,
            "Found",
            Message(),
            io.BytesIO(b"must not be read"),
        )
        client = HttpClient(attempts=1, opener=_RaisingOpener(redirect))

        client.probe_media("https://www.googleapis.com/drive/v3/files/abc?alt=media")

        self.assertTrue(redirect.fp is None or redirect.fp.closed)

    def test_media_probe_does_not_retry_transient_errors(self):
        transient = urllib.error.HTTPError(
            "https://www.googleapis.com/drive/v3/files/abc?alt=media",
            503,
            "Unavailable",
            Message(),
            io.BytesIO(b""),
        )
        opener = _RaisingOpener(transient)
        client = HttpClient(attempts=3, opener=opener)

        with self.assertRaises(DriveError):
            client.probe_media("https://www.googleapis.com/drive/v3/files/abc?alt=media")

        self.assertEqual(1, opener.calls)

    def test_media_probe_maps_403_without_retry(self):
        opener = _RaisingOpener(
            _http_403({"error": {"errors": [{"reason": "downloadQuotaExceeded"}]}})
        )
        client = HttpClient(attempts=3, opener=opener)

        with self.assertRaises(DriveError) as context:
            client.probe_media("https://www.googleapis.com/drive/v3/files/abc?alt=media")

        self.assertIn("download quota", str(context.exception))
        self.assertEqual(1, opener.calls)

    def test_403_with_known_reason_uses_specific_message_without_retry(self):
        opener = _RaisingOpener(
            _http_403({"error": {"errors": [{"reason": "downloadQuotaExceeded"}]}})
        )
        client = HttpClient(attempts=3, opener=opener)

        with self.assertRaises(DriveError) as context:
            client.get_json("https://www.googleapis.com/drive/v3/files/abc")

        self.assertIn("download quota", str(context.exception))
        self.assertEqual(1, opener.calls)

    def test_403_checks_all_structured_error_reasons(self):
        exc = _http_403({
            "error": {
                "errors": [
                    {"reason": "unknownReason"},
                    {"reason": "insufficientFilePermissions"},
                ]
            }
        })
        self.assertEqual(
            http_client._KNOWN_403_REASONS["insufficientFilePermissions"],
            http_client._describe_403(exc),
        )

    def test_403_falls_back_to_status_field(self):
        exc = _http_403({"error": {"status": "rateLimitExceeded"}})
        self.assertEqual(
            http_client._KNOWN_403_REASONS["rateLimitExceeded"],
            http_client._describe_403(exc),
        )

    def test_403_with_unknown_reason_uses_existing_generic_message(self):
        client = HttpClient(
            attempts=1,
            opener=_RaisingOpener(
                _http_403({"error": {"errors": [{"reason": "somethingUnrecognized"}]}})
            ),
        )

        with self.assertRaises(DriveError) as context:
            client.get_json("https://www.googleapis.com/drive/v3/files/abc")

        self.assertEqual(http_client._GENERIC_403_MESSAGE, str(context.exception))

    def test_describe_403_returns_none_for_malformed_empty_or_oversized_body(self):
        bodies = (
            b"",
            b"not json",
            b"{" + b"a" * http_client._MAX_ERROR_BODY_BYTES + b"}",
        )
        for body in bodies:
            with self.subTest(size=len(body)):
                self.assertIsNone(http_client._describe_403(_http_403(raw_body=body)))

    def test_describe_403_returns_none_for_unexpected_shape(self):
        documents = (
            [],
            {"unexpected": "shape"},
            {"error": []},
            {"error": {"errors": [None, {"reason": 123}]}},
        )
        for document in documents:
            with self.subTest(document=document):
                self.assertIsNone(http_client._describe_403(_http_403(document)))

    def test_describe_403_never_raises_for_unreadable_body(self):
        class _UnreadableError:
            def read(self, size):
                raise OSError("boom")

        self.assertIsNone(http_client._describe_403(_UnreadableError()))


if __name__ == "__main__":
    unittest.main()
