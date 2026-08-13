import importlib.util
import pathlib
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sharedgdrive_package", ROOT / "package.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load package.py")
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class PackageTests(unittest.TestCase):
    def test_rejects_non_semantic_manifest_versions(self):
        for version in (".", "1..2", "1.2", "1.2.3.4"):
            root = mock.Mock()
            root.attrib = {"version": version}
            parsed = mock.Mock()
            parsed.getroot.return_value = root
            with self.subTest(version=version), mock.patch.object(
                PACKAGE.ElementTree,
                "parse",
                return_value=parsed,
            ), self.assertRaises(RuntimeError):
                PACKAGE._version()

    def test_package_allowlist_excludes_tests_and_development_files(self):
        relative = {path.relative_to(ROOT).as_posix() for path in PACKAGE._package_files()}
        self.assertIn("addon.xml", relative)
        self.assertIn("DESIGN_REVIEW.md", relative)
        self.assertIn("resources/lib/drive.py", relative)
        self.assertNotIn("package.py", relative)
        self.assertFalse(any(path.startswith("tests/") for path in relative))
        self.assertEqual(PACKAGE.ALLOWED_PATHS, relative)

    def test_archive_paths_use_addon_id_instead_of_checkout_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = PACKAGE.build(directory)
            with zipfile.ZipFile(archive_path, "r") as archive:
                names = archive.namelist()
                self.assertIn("plugin.video.sharedgdrive/addon.xml", names)
                self.assertTrue(all(name.startswith("plugin.video.sharedgdrive/") for name in names))
                info = archive.getinfo("plugin.video.sharedgdrive/addon.xml")
                self.assertEqual(PACKAGE.ZIP_TIMESTAMP, info.date_time)


if __name__ == "__main__":
    unittest.main()
