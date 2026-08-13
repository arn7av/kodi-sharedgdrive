import pathlib
import sys
import unittest
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resources.lib.drive import DriveClient
from resources.lib.errors import AccessBoundaryError, ConfigurationError, DriveError, UnauthorizedError


class RefreshingHttp:
    def __init__(self):
        self.calls = []

    def get_json(self, url, headers=None):
        self.calls.append(headers["Authorization"])
        if len(self.calls) == 1:
            raise UnauthorizedError("rejected")
        return {"files": []}


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.probe_calls = []

    def get_json(self, url, headers=None):
        self.calls.append((url, headers))
        return self.responses.pop(0)

    def probe_media(self, url, headers=None):
        self.probe_calls.append((url, headers))


class RefreshingProbeHttp(FakeHttp):
    def probe_media(self, url, headers=None):
        self.probe_calls.append((url, headers))
        if len(self.probe_calls) == 1:
            raise UnauthorizedError("rejected")


class FakeFolderCache:
    def __init__(self, cached=None):
        self.cached = cached
        self.put_call = None

    def get(self, fingerprint, folder_id):
        return self.cached

    def put(self, fingerprint, folder_id, items):
        self.put_call = (fingerprint, folder_id, items)


class DriveClientTests(unittest.TestCase):
    def test_lists_only_folders_and_videos_from_configured_drive(self):
        http = FakeHttp(
            [
                {
                    "files": [
                        {"id": "folder_1", "name": "Folder", "mimeType": "application/vnd.google-apps.folder", "driveId": "drive_1"},
                        {"id": "video_1", "name": "Movie", "mimeType": "video/mp4", "driveId": "drive_1", "capabilities": {"canDownload": True}},
                        {"id": "text_1", "name": "Notes", "mimeType": "text/plain", "driveId": "drive_1"},
                    ]
                }
            ]
        )
        client = DriveClient(http, "secret-token", "drive_1")

        items = client.list_folder("drive_1")

        self.assertEqual(["folder_1", "video_1"], [item["id"] for item in items])
        parsed = urllib.parse.urlsplit(http.calls[0][0])
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(["drive"], query["corpora"])
        self.assertEqual(["drive_1"], query["driveId"])
        self.assertEqual(["true"], query["supportsAllDrives"])
        self.assertEqual(["true"], query["includeItemsFromAllDrives"])
        self.assertEqual(
            ["'drive_1' in parents and trashed=false and (mimeType='application/vnd.google-apps.folder' or mimeType contains 'video/')"],
            query["q"],
        )
        self.assertEqual("Bearer secret-token", http.calls[0][1]["Authorization"])

    def test_uses_valid_cached_folder_without_http_request(self):
        cached = [{"id": "video_1", "name": "Movie", "mimeType": "video/mp4", "driveId": "drive_1", "trashed": False, "capabilities": {"canDownload": True}}]
        cache = FakeFolderCache(cached)
        http = FakeHttp([])
        client = DriveClient(http, "token", "drive_1", folder_cache=cache, cache_fingerprint="fingerprint")

        self.assertEqual(cached, client.list_folder("drive_1"))
        self.assertEqual([], http.calls)

    def test_validates_cached_items_against_drive_boundary(self):
        cached = [{"id": "video_1", "mimeType": "video/mp4", "driveId": "other_drive"}]
        client = DriveClient(
            FakeHttp([]),
            "token",
            "drive_1",
            folder_cache=FakeFolderCache(cached),
            cache_fingerprint="fingerprint",
        )

        with self.assertRaises(AccessBoundaryError):
            client.list_folder("drive_1")

    def test_rejects_google_flagged_incomplete_folder_results(self):
        http = FakeHttp([{"incompleteSearch": True, "files": []}])
        client = DriveClient(http, "token", "drive_1")

        with self.assertRaises(DriveError) as context:
            client.list_folder("drive_1")

        self.assertIn("incomplete", str(context.exception))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(http.calls[0][0]).query)
        self.assertIn("incompleteSearch", query["fields"][0])

    def test_stores_successful_interactive_listing_in_cache(self):
        cache = FakeFolderCache()
        http = FakeHttp([{"files": [{"id": "video_1", "name": "Movie", "mimeType": "video/mp4", "driveId": "drive_1", "capabilities": {"canDownload": True}}]}])
        client = DriveClient(http, "token", "drive_1", folder_cache=cache, cache_fingerprint="fingerprint")

        items = client.list_folder("drive_1")

        self.assertEqual(("fingerprint", "drive_1", items), cache.put_call)

    def test_refreshes_token_once_after_unauthorized_response(self):
        http = RefreshingHttp()
        refresh_calls = []
        client = DriveClient(
            http,
            "old-token",
            "drive_1",
            token_refresher=lambda: refresh_calls.append(True) or "new-token",
        )

        self.assertEqual([], client.list_folder("drive_1"))
        self.assertEqual(["Bearer old-token", "Bearer new-token"], http.calls)
        self.assertEqual([True], refresh_calls)

    def test_rejects_item_from_another_drive(self):
        http = FakeHttp([{"files": [{"id": "video_1", "mimeType": "video/mp4", "driveId": "other_drive"}]}])
        client = DriveClient(http, "token", "drive_1")

        with self.assertRaises(AccessBoundaryError):
            client.list_folder("drive_1")

    def test_validates_nested_folder_before_listing_it(self):
        http = FakeHttp(
            [
                {"id": "folder_1", "name": "Folder", "mimeType": "application/vnd.google-apps.folder", "driveId": "drive_1", "trashed": False},
                {"files": []},
            ]
        )
        client = DriveClient(http, "token", "drive_1")

        client.list_folder("folder_1")

        self.assertIn("/files/folder_1?", http.calls[0][0])
        self.assertIn("/files?", http.calls[1][0])

    def test_walks_nested_folders_without_refetching_folder_metadata(self):
        http = FakeHttp(
            [
                {"files": [{"id": "folder_1", "name": "Folder", "mimeType": "application/vnd.google-apps.folder", "driveId": "drive_1"}]},
                {"files": [{"id": "video_1", "name": "Movie", "mimeType": "video/mp4", "driveId": "drive_1", "capabilities": {"canDownload": True}}]},
            ]
        )
        client = DriveClient(http, "token", "drive_1")

        results = list(client.walk_videos())

        self.assertEqual(1, len(results))
        folders, video = results[0]
        self.assertEqual(["folder_1"], [folder["id"] for folder in folders])
        self.assertEqual("video_1", video["id"])
        self.assertEqual(2, len(http.calls))
        self.assertTrue(all("/files?" in call[0] for call in http.calls))

    def test_builds_authenticated_original_playback_url(self):
        http = FakeHttp(
            [
                {
                    "id": "video_1",
                    "name": "Movie",
                    "mimeType": "video/mp4",
                    "driveId": "drive_1",
                    "trashed": False,
                    "capabilities": {"canDownload": True},
                }
            ]
        )
        client = DriveClient(http, "token with space", "drive_1")

        url, item = client.get_playback_url("video_1")

        self.assertEqual("video_1", item["id"])
        self.assertIn("alt=media", url)
        self.assertIn("supportsAllDrives=true", url)
        self.assertTrue(url.endswith("|Authorization=Bearer%20token%20with%20space"))

    def test_optional_media_preflight_uses_media_url_and_authorization(self):
        http = FakeHttp(
            [
                {
                    "id": "video_1",
                    "name": "Movie",
                    "mimeType": "video/mp4",
                    "driveId": "drive_1",
                    "trashed": False,
                    "capabilities": {"canDownload": True},
                }
            ]
        )
        client = DriveClient(http, "token", "drive_1")

        client.get_playback_url("video_1", preflight=True)

        self.assertEqual(1, len(http.probe_calls))
        self.assertIn("/files/video_1?", http.probe_calls[0][0])
        self.assertIn("alt=media", http.probe_calls[0][0])
        self.assertEqual("Bearer token", http.probe_calls[0][1]["Authorization"])

    def test_media_preflight_refreshes_rejected_token_once(self):
        http = RefreshingProbeHttp(
            [
                {
                    "id": "video_1",
                    "name": "Movie",
                    "mimeType": "video/mp4",
                    "driveId": "drive_1",
                    "trashed": False,
                    "capabilities": {"canDownload": True},
                }
            ]
        )
        refresh_calls = []
        client = DriveClient(
            http,
            "old-token",
            "drive_1",
            token_refresher=lambda: refresh_calls.append(True) or "new-token",
        )

        playback_url, _ = client.get_playback_url("video_1", preflight=True)

        self.assertEqual(
            ["Bearer old-token", "Bearer new-token"],
            [headers["Authorization"] for _, headers in http.probe_calls],
        )
        self.assertEqual([True], refresh_calls)
        self.assertTrue(playback_url.endswith("|Authorization=Bearer%20new-token"))

    def test_playback_does_not_probe_media_by_default(self):
        http = FakeHttp(
            [
                {
                    "id": "video_1",
                    "name": "Movie",
                    "mimeType": "video/mp4",
                    "driveId": "drive_1",
                    "trashed": False,
                    "capabilities": {"canDownload": True},
                }
            ]
        )
        DriveClient(http, "token", "drive_1").get_playback_url("video_1")

        self.assertEqual([], http.probe_calls)

    def test_rejects_non_video_playback(self):
        http = FakeHttp(
            [
                {
                    "id": "text_1",
                    "mimeType": "text/plain",
                    "driveId": "drive_1",
                    "trashed": False,
                    "capabilities": {"canDownload": True},
                }
            ]
        )
        client = DriveClient(http, "token", "drive_1")

        with self.assertRaises(DriveError):
            client.get_playback_url("text_1")

    def test_rejects_malformed_identifiers(self):
        with self.assertRaises(ConfigurationError):
            DriveClient(FakeHttp([]), "token", "drive id with spaces")

        client = DriveClient(FakeHttp([]), "token", "drive_1")
        with self.assertRaises(ConfigurationError):
            client.list_folder("folder' or trashed=true")


if __name__ == "__main__":
    unittest.main()
