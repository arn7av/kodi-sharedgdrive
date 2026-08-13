import json
import pathlib
import sys
import unittest
import xml.etree.ElementTree as ElementTree

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resources.lib.config import KodiConfig
from resources.lib.errors import ConfigurationError


class FakeAddon:
    def __init__(self):
        self.values = {}

    def getSettingString(self, key):
        return self.values.get(key, "")

    def setSettingString(self, key, value):
        self.values[key] = value

    def getSettingBool(self, key):
        return self.values.get(key, "false") is True or self.values.get(key) == "true"


class FakeFile:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.value if size < 0 else self.value[:size]


class FakeVfs:
    def __init__(self, files):
        self.files = files

    def File(self, path):
        return FakeFile(self.files[path])


class ConfigTests(unittest.TestCase):
    def test_imports_only_required_service_account_fields(self):
        document = {
            "type": "service_account",
            "project_id": "should-not-be-stored",
            "private_key_id": "should-not-be-stored",
            "client_email": "viewer@example.iam.gserviceaccount.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n",
            "client_x509_cert_url": "https://example.invalid/cert",
        }
        addon = FakeAddon()
        config = KodiConfig(addon, FakeVfs({"credentials.json": json.dumps(document)}))

        imported = config.import_credentials("credentials.json")

        self.assertTrue(imported)
        self.assertEqual(
            {"client_email", "private_key"},
            set(addon.values),
        )
        self.assertNotIn("project_id", addon.values)

    def test_rejects_non_service_account_json(self):
        addon = FakeAddon()
        config = KodiConfig(addon, FakeVfs({"credentials.json": json.dumps({"type": "authorized_user"})}))

        with self.assertRaises(ConfigurationError):
            config.import_credentials("credentials.json")
        self.assertEqual({}, addon.values)

    def test_cancelled_import_changes_nothing(self):
        addon = FakeAddon()
        config = KodiConfig(addon, FakeVfs({}))

        self.assertFalse(config.import_credentials(""))
        self.assertEqual({}, addon.values)

    def test_optional_performance_features_are_disabled_by_default(self):
        config = KodiConfig(FakeAddon(), FakeVfs({}))

        self.assertFalse(config.playback_preflight_enabled)
        self.assertFalse(config.snapshot_export_enabled)
        self.assertFalse(config.snapshot_auto_prune)
        self.assertEqual("", config.snapshot_export_folder)

        settings = ElementTree.parse(ROOT / "resources/settings.xml")
        defaults = {
            setting.attrib["id"]: setting.findtext("default")
            for setting in settings.findall(".//setting")
        }
        self.assertEqual("false", defaults["playback_preflight_enabled"])
        self.assertEqual("false", defaults["snapshot_auto_prune"])

    def test_rejects_export_folder_with_embedded_credentials_before_storage(self):
        addon = FakeAddon()
        config = KodiConfig(addon, FakeVfs({}))

        with self.assertRaises(ConfigurationError):
            config.snapshot_export_folder = "smb://user:password@example/share"
        self.assertNotIn("snapshot_export_folder", addon.values)

    def test_clears_legacy_stored_export_folder_with_embedded_credentials(self):
        addon = FakeAddon()
        addon.values["snapshot_export_folder"] = "smb://user:password@example/share"
        config = KodiConfig(addon, FakeVfs({}))

        with self.assertRaises(ConfigurationError):
            _ = config.snapshot_export_folder
        self.assertEqual("", addon.values["snapshot_export_folder"])

    def test_clear_credentials(self):
        addon = FakeAddon()
        addon.values = {"client_email": "email", "private_key": "key", "shared_drive_id": "drive_1"}
        config = KodiConfig(addon, FakeVfs({}))

        config.clear_credentials()

        self.assertEqual("", addon.values["client_email"])
        self.assertEqual("", addon.values["private_key"])
        self.assertEqual("drive_1", addon.values["shared_drive_id"])


if __name__ == "__main__":
    unittest.main()
