import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resources.lib.errors import DriveError
from resources.lib import strm_exporter
from resources.lib.strm_exporter import SnapshotExporter, StaleExportManager


class MemoryFile:
    def __init__(self, vfs, path, mode="r"):
        self.vfs = vfs
        self.path = path
        self.mode = mode
        self.value = "" if "w" in mode else vfs.files[path]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if "w" in self.mode:
            self.vfs.files[self.path] = self.value
        return False

    def read(self, size=-1):
        return self.value if size < 0 else self.value[:size]

    def write(self, value):
        self.value += value


class MemoryVfs:
    def __init__(self):
        self.files = {}
        self.directories = {"exports"}
        self.failed_deletes = set()

    def exists(self, path):
        return path in self.files or path in self.directories

    def mkdirs(self, path):
        self.directories.add(path)
        return True

    def File(self, path, mode="r"):
        return MemoryFile(self, path, mode)

    def delete(self, path):
        if path in self.failed_deletes:
            return False
        self.files.pop(path, None)
        return True

    def rename(self, source, destination):
        if source not in self.files:
            return False
        self.files[destination] = self.files.pop(source)
        return True


class FakeDrive:
    shared_drive_id = "drive_1"

    def __init__(self, videos=None):
        self.videos = videos

    def walk_videos(self, cancelled=None):
        if self.videos is not None:
            yield from self.videos
            return
        folder = {"id": "folder_1", "name": "Movies"}
        yield [folder], {"id": "video_1", "name": "Example (2026).mkv"}


class FailingDrive(FakeDrive):
    def walk_videos(self, cancelled=None):
        raise DriveError("enumeration failed")
        yield


class ExporterTests(unittest.TestCase):
    def test_exports_plugin_url_and_manifest(self):
        vfs = MemoryVfs()
        exporter = SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/")

        result = exporter.export()

        self.assertEqual(1, result["written"])
        path = "exports/Movies/Example (2026).strm"
        self.assertEqual(
            "plugin://plugin.video.sharedgdrive/?action=play&file_id=video_1\n",
            vfs.files[path],
        )
        manifest = json.loads(vfs.files["exports/.sharedgdrive-export.json"])
        self.assertEqual("video_1", manifest["files"]["Movies/Example (2026).strm"])

    def test_reexport_retains_owned_file_without_rewriting_it(self):
        vfs = MemoryVfs()
        SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()
        path = "exports/Movies/Example (2026).strm"
        original = vfs.files[path]

        result = SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()

        self.assertEqual(1, result["skipped"])
        self.assertNotIn("overwritten", result)
        self.assertEqual(original, vfs.files[path])

    def test_does_not_overwrite_owned_file_if_user_modified_it(self):
        vfs = MemoryVfs()
        SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()
        path = "exports/Movies/Example (2026).strm"
        vfs.files[path] = "manually changed"

        result = SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()

        self.assertEqual(1, result["skipped"])
        self.assertEqual("manually changed", vfs.files[path])

    def test_reports_and_tracks_stale_owned_file_without_deleting_it(self):
        vfs = MemoryVfs()
        SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()
        path = "exports/Movies/Example (2026).strm"

        result = SnapshotExporter(FakeDrive(videos=[]), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()

        self.assertEqual(1, result["stale"])
        self.assertIn(path, vfs.files)
        manifest = json.loads(vfs.files["exports/.sharedgdrive-export.json"])
        self.assertEqual("video_1", manifest["stale_files"]["Movies/Example (2026).strm"])

    def test_auto_prune_removes_stale_owned_file_after_complete_export(self):
        vfs = MemoryVfs()
        SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()

        result = SnapshotExporter(
            FakeDrive(videos=[]),
            vfs,
            "exports",
            "plugin://plugin.video.sharedgdrive/",
        ).export(auto_prune=True)

        self.assertEqual(1, result["auto_pruned"])
        self.assertEqual(0, result["cleanup_skipped"])
        self.assertEqual(0, result["stale"])
        self.assertNotIn("exports/Movies/Example (2026).strm", vfs.files)

    def test_cancelled_export_never_auto_prunes_previous_owned_files(self):
        vfs = MemoryVfs()
        SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()

        result = SnapshotExporter(
            FakeDrive(videos=[]),
            vfs,
            "exports",
            "plugin://plugin.video.sharedgdrive/",
        ).export(auto_prune=True, cancelled=lambda: True)

        self.assertTrue(result["cancelled"])
        self.assertNotIn("auto_pruned", result)
        self.assertIn("exports/Movies/Example (2026).strm", vfs.files)

    def test_failed_export_never_auto_prunes_previous_owned_files(self):
        vfs = MemoryVfs()
        SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()

        with self.assertRaises(DriveError):
            SnapshotExporter(
                FailingDrive(),
                vfs,
                "exports",
                "plugin://plugin.video.sharedgdrive/",
            ).export(auto_prune=True)

        self.assertIn("exports/Movies/Example (2026).strm", vfs.files)

    def test_modified_stale_file_loses_exporter_ownership(self):
        vfs = MemoryVfs()
        SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()
        path = "exports/Movies/Example (2026).strm"
        vfs.files[path] = "manually changed"

        result = SnapshotExporter(FakeDrive(videos=[]), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()

        self.assertEqual(0, result["stale"])
        manifest = json.loads(vfs.files["exports/.sharedgdrive-export.json"])
        self.assertEqual({}, manifest["stale_files"])

    def test_stale_manager_removes_only_selected_owned_file(self):
        vfs = MemoryVfs()
        SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()
        SnapshotExporter(FakeDrive(videos=[]), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()
        manager = StaleExportManager(vfs, "exports", "plugin://plugin.video.sharedgdrive/", "drive_1")
        stale = manager.list_owned_stale()

        result = manager.remove(stale)

        self.assertEqual({"removed": 1, "skipped": 0}, result)
        self.assertNotIn("exports/Movies/Example (2026).strm", vfs.files)
        manifest = json.loads(vfs.files["exports/.sharedgdrive-export.json"])
        self.assertEqual({}, manifest["stale_files"])

    def test_auto_prune_bypasses_review_dialog_limit(self):
        vfs = MemoryVfs()
        stale_files = {}
        for index in range(strm_exporter._MAX_REVIEWABLE_STALE + 1):
            relative_path = "Video {0}.strm".format(index)
            file_id = "video_{0}".format(index)
            stale_files[relative_path] = file_id
            vfs.files["exports/" + relative_path] = (
                "plugin://plugin.video.sharedgdrive/?action=play&file_id={0}\n".format(file_id)
            )
        vfs.files["exports/.sharedgdrive-export.json"] = json.dumps({
            "version": 2,
            "drive_id": "drive_1",
            "files": {},
            "stale_files": stale_files,
        })
        manager = StaleExportManager(
            vfs,
            "exports",
            "plugin://plugin.video.sharedgdrive/",
            "drive_1",
        )

        with self.assertRaises(DriveError):
            manager.list_owned_stale()
        result = manager.remove_all_owned_stale()

        self.assertEqual(strm_exporter._MAX_REVIEWABLE_STALE + 1, result["removed"])
        self.assertEqual(0, result["remaining"])
        self.assertFalse(result["cancelled"])

    def test_auto_prune_revalidates_exact_content_before_deletion(self):
        vfs = MemoryVfs()
        SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()
        SnapshotExporter(FakeDrive(videos=[]), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()
        path = "exports/Movies/Example (2026).strm"
        vfs.files[path] = "manually changed"
        manager = StaleExportManager(vfs, "exports", "plugin://plugin.video.sharedgdrive/", "drive_1")

        result = manager.remove_all_owned_stale()

        self.assertEqual(0, result["removed"])
        self.assertEqual(1, result["skipped"])
        self.assertEqual(0, result["remaining"])
        self.assertEqual("manually changed", vfs.files[path])

    def test_auto_prune_retains_manifest_entry_when_local_delete_fails(self):
        vfs = MemoryVfs()
        SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()
        SnapshotExporter(FakeDrive(videos=[]), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()
        path = "exports/Movies/Example (2026).strm"
        vfs.failed_deletes.add(path)
        manager = StaleExportManager(vfs, "exports", "plugin://plugin.video.sharedgdrive/", "drive_1")

        result = manager.remove_all_owned_stale()

        self.assertEqual(0, result["removed"])
        self.assertEqual(1, result["skipped"])
        self.assertEqual(1, result["remaining"])
        manifest = json.loads(vfs.files["exports/.sharedgdrive-export.json"])
        self.assertEqual("video_1", manifest["stale_files"]["Movies/Example (2026).strm"])

    def test_auto_prune_cancellation_checkpoints_and_retains_remaining_entries(self):
        vfs = MemoryVfs()
        stale_files = {}
        for index in range(600):
            relative_path = "Video {0}.strm".format(index)
            file_id = "video_{0}".format(index)
            stale_files[relative_path] = file_id
            vfs.files["exports/" + relative_path] = (
                "plugin://plugin.video.sharedgdrive/?action=play&file_id={0}\n".format(file_id)
            )
        vfs.files["exports/.sharedgdrive-export.json"] = json.dumps({
            "version": 2,
            "drive_id": "drive_1",
            "files": {},
            "stale_files": stale_files,
        })
        checks = []
        manager = StaleExportManager(vfs, "exports", "plugin://plugin.video.sharedgdrive/", "drive_1")

        result = manager.remove_all_owned_stale(
            cancelled=lambda: checks.append(True) or len(checks) > 510,
        )

        self.assertEqual(510, result["removed"])
        self.assertEqual(90, result["remaining"])
        self.assertTrue(result["cancelled"])
        manifest = json.loads(vfs.files["exports/.sharedgdrive-export.json"])
        self.assertEqual(90, len(manifest["stale_files"]))

    def test_stale_manager_skips_file_changed_after_review(self):
        vfs = MemoryVfs()
        SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()
        SnapshotExporter(FakeDrive(videos=[]), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()
        manager = StaleExportManager(vfs, "exports", "plugin://plugin.video.sharedgdrive/", "drive_1")
        stale = manager.list_owned_stale()
        path = "exports/Movies/Example (2026).strm"
        vfs.files[path] = "manually changed"

        result = manager.remove(stale)

        self.assertEqual({"removed": 0, "skipped": 1}, result)
        self.assertEqual("manually changed", vfs.files[path])

    def test_rejects_manifest_path_traversal(self):
        vfs = MemoryVfs()
        vfs.files["exports/.sharedgdrive-export.json"] = json.dumps({
            "version": 2,
            "drive_id": "drive_1",
            "files": {},
            "stale_files": {"../outside.strm": "video_1"},
        })
        manager = StaleExportManager(vfs, "exports", "plugin://plugin.video.sharedgdrive/", "drive_1")

        with self.assertRaises(DriveError):
            manager.list_owned_stale()

    def test_rejects_destination_with_embedded_credentials(self):
        with self.assertRaises(Exception):
            SnapshotExporter(
                FakeDrive(),
                MemoryVfs(),
                "smb://user:password@example/share",
                "plugin://plugin.video.sharedgdrive/",
            )

    def test_rejects_active_stale_manifest_overlap(self):
        vfs = MemoryVfs()
        vfs.files["exports/.sharedgdrive-export.json"] = json.dumps({
            "version": 2,
            "drive_id": "drive_1",
            "files": {"Movie.strm": "video_1"},
            "stale_files": {"movie.strm": "video_1"},
        })
        manager = StaleExportManager(vfs, "exports", "plugin://plugin.video.sharedgdrive/", "drive_1")

        with self.assertRaises(DriveError):
            manager.list_owned_stale()

    def test_manifest_capacity_fails_before_installing_unowned_file(self):
        vfs = MemoryVfs()
        videos = [
            ([], {"id": "video_1", "name": "One.mkv"}),
            ([], {"id": "video_2", "name": "Two.mkv"}),
        ]
        exporter = SnapshotExporter(FakeDrive(videos=videos), vfs, "exports", "plugin://plugin.video.sharedgdrive/")

        with mock.patch.object(strm_exporter, "_MAX_MANIFEST_ENTRIES", 1), self.assertRaises(DriveError):
            exporter.export()

        self.assertIn("exports/One.strm", vfs.files)
        self.assertNotIn("exports/Two.strm", vfs.files)
        manifest = json.loads(vfs.files["exports/.sharedgdrive-export.json"])
        self.assertEqual({"One.strm": "video_1"}, manifest["files"])

    def test_manifest_byte_capacity_fails_before_installing_unowned_file(self):
        vfs = MemoryVfs()
        videos = [
            ([], {"id": "video_1", "name": "One.mkv"}),
            ([], {"id": "video_2", "name": "Two With A Long Name.mkv"}),
        ]
        exporter = SnapshotExporter(FakeDrive(videos=videos), vfs, "exports", "plugin://plugin.video.sharedgdrive/")
        one_entry_size = exporter._manifest.active_manifest_size({"One.strm": "video_1"})

        with mock.patch.object(strm_exporter, "_MAX_MANIFEST_BYTES", one_entry_size), self.assertRaises(DriveError):
            exporter.export()

        self.assertIn("exports/One.strm", vfs.files)
        self.assertNotIn("exports/Two With A Long Name.strm", vfs.files)
        manifest = json.loads(vfs.files["exports/.sharedgdrive-export.json"])
        self.assertEqual({"One.strm": "video_1"}, manifest["files"])

    def test_never_overwrites_unowned_existing_strm(self):
        vfs = MemoryVfs()
        vfs.directories.add("exports/Movies")
        path = "exports/Movies/Example (2026).strm"
        vfs.files[path] = "unrelated"

        result = SnapshotExporter(FakeDrive(), vfs, "exports", "plugin://plugin.video.sharedgdrive/").export()

        self.assertEqual(1, result["skipped"])
        self.assertEqual("unrelated", vfs.files[path])


if __name__ == "__main__":
    unittest.main()
