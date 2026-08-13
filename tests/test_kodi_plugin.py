import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

xbmc = types.ModuleType("xbmc")
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcgui = types.ModuleType("xbmcgui")
xbmcplugin = types.ModuleType("xbmcplugin")
xbmcvfs = types.ModuleType("xbmcvfs")
xbmc.Player = lambda: None
xbmcgui.INPUT_ALPHANUM = 0
xbmcgui.Dialog = lambda: None
xbmcgui.ListItem = lambda **kwargs: None
sys.modules.setdefault("xbmc", xbmc)
sys.modules.setdefault("xbmcaddon", xbmcaddon)
sys.modules.setdefault("xbmcgui", xbmcgui)
sys.modules.setdefault("xbmcplugin", xbmcplugin)
sys.modules.setdefault("xbmcvfs", xbmcvfs)

from resources.lib import kodi_plugin


class FakeAddon:
    def getLocalizedString(self, identifier):
        return "string-{0}".format(identifier)


class FakeConfig:
    is_complete = True
    playback_preflight_enabled = False


class FakeListItem:
    def __init__(self, label="", path=""):
        self.label = label
        self.path = path
        self.properties = {}
        self.mime_type = None

    def setProperty(self, key, value):
        self.properties[key] = value

    def setMimeType(self, value):
        self.mime_type = value


class FakeDialog:
    def __init__(self, reference, confirmation=True):
        self.reference = reference
        self.confirmation = confirmation
        self.confirmations = []

    def input(self, heading, type=None):
        return self.reference

    def yesno(self, heading, message):
        self.confirmations.append((heading, message))
        return self.confirmation


class FakeDrive:
    shared_drive_id = "drive_1"

    def __init__(self, item):
        self.item = item
        self.resolved = []

    def get_debug_playback_url(self, file_id, allow_outside=False, preflight=False):
        self.resolved.append((file_id, allow_outside, preflight))
        if self.item.get("driveId") != self.shared_drive_id and not allow_outside:
            raise kodi_plugin.AccessBoundaryError("outside")
        return (
            "https://www.googleapis.com/drive/v3/files/{0}?alt=media|Authorization=Bearer%20token".format(file_id),
            self.item,
        )


class FailingFinalDrive(FakeDrive):
    def get_debug_playback_url(self, file_id, allow_outside=False, preflight=False):
        raise kodi_plugin.PluginError("The item changed before playback.")


class FakePlayer:
    def __init__(self):
        self.calls = []

    def play(self, url, list_item):
        self.calls.append((url, list_item))


class KodiDebugPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.original_dialog = kodi_plugin.xbmcgui.Dialog
        self.original_list_item = kodi_plugin.xbmcgui.ListItem
        self.original_player = kodi_plugin.xbmc.Player
        kodi_plugin.xbmcgui.ListItem = FakeListItem
        self.player = FakePlayer()
        kodi_plugin.xbmc.Player = lambda: self.player

    def tearDown(self):
        kodi_plugin.xbmcgui.Dialog = self.original_dialog
        kodi_plugin.xbmcgui.ListItem = self.original_list_item
        kodi_plugin.xbmc.Player = self.original_player

    def _plugin(self, drive, dialog):
        plugin = kodi_plugin.KodiPlugin.__new__(kodi_plugin.KodiPlugin)
        plugin._config = FakeConfig()
        plugin._addon = FakeAddon()
        drive_calls = []

        def drive_client(**kwargs):
            drive_calls.append(kwargs)
            return drive

        plugin._drive_client = drive_client
        kodi_plugin.xbmcgui.Dialog = lambda: dialog
        return plugin, drive_calls

    def test_cancelled_input_does_not_create_drive_client_or_play(self):
        drive = FakeDrive({})
        plugin, drive_calls = self._plugin(drive, FakeDialog(""))

        plugin._debug_play()

        self.assertEqual([], drive_calls)
        self.assertEqual([], drive.resolved)
        self.assertEqual([], self.player.calls)

    def test_in_drive_item_plays_without_boundary_confirmation(self):
        item = {
            "id": "video_1",
            "name": "Movie",
            "mimeType": "video/mp4",
            "driveId": "drive_1",
            "capabilities": {"canDownload": True},
        }
        drive = FakeDrive(item)
        dialog = FakeDialog("https://drive.google.com/file/d/video_1/view")
        plugin, drive_calls = self._plugin(drive, dialog)

        plugin._debug_play()

        self.assertEqual([], dialog.confirmations)
        self.assertEqual([("video_1", False, False)], drive.resolved)
        self.assertEqual(1, len(self.player.calls))
        expected = {"use_folder_cache": False, "minimum_token_remaining": 55 * 60}
        self.assertEqual([expected], drive_calls)

    def test_outside_drive_item_does_not_play_when_confirmation_declined(self):
        item = {
            "id": "video_1",
            "name": "Outside Movie",
            "mimeType": "video/mp4",
            "driveId": "other_drive",
            "capabilities": {"canDownload": True},
        }
        drive = FakeDrive(item)
        dialog = FakeDialog("video_1", confirmation=False)
        plugin, _ = self._plugin(drive, dialog)

        plugin._debug_play()

        self.assertEqual(1, len(dialog.confirmations))
        self.assertEqual([("video_1", False, False)], drive.resolved)
        self.assertEqual([], self.player.calls)

    def test_outside_drive_item_plays_once_after_confirmation(self):
        item = {
            "id": "video_1",
            "name": "Shared Movie",
            "mimeType": "video/mp4",
            "capabilities": {"canDownload": True},
        }
        drive = FakeDrive(item)
        dialog = FakeDialog("video_1", confirmation=True)
        plugin, _ = self._plugin(drive, dialog)

        plugin._debug_play()

        self.assertEqual(1, len(dialog.confirmations))
        self.assertEqual(
            [("video_1", False, False), ("video_1", True, False)],
            drive.resolved,
        )
        self.assertEqual(1, len(self.player.calls))
        self.assertIn(
            "https://www.googleapis.com/drive/v3/files/video_1?alt=media",
            self.player.calls[0][0],
        )
        _, list_item = self.player.calls[0]
        self.assertEqual("Shared Movie", list_item.label)
        self.assertEqual("video/mp4", list_item.mime_type)
        self.assertEqual("true", list_item.properties["IsPlayable"])

    def test_final_metadata_failure_does_not_start_playback(self):
        item = {
            "id": "video_1",
            "name": "Changed Movie",
            "mimeType": "video/mp4",
            "driveId": "other_drive",
            "capabilities": {"canDownload": True},
        }
        first_drive = FakeDrive(item)
        final_drive = FailingFinalDrive(item)
        dialog = FakeDialog("video_1", confirmation=True)
        plugin, _ = self._plugin(first_drive, dialog)
        clients = iter((first_drive, final_drive))
        plugin._drive_client = lambda **kwargs: next(clients)

        with self.assertRaises(kodi_plugin.PluginError):
            plugin._debug_play()

        self.assertEqual([], self.player.calls)


if __name__ == "__main__":
    unittest.main()
