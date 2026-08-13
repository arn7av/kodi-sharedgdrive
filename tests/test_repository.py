import hashlib
import importlib.util
import pathlib
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sharedgdrive_repository", ROOT / "build_repository.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load build_repository.py")
REPOSITORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPOSITORY)


def _addon_archive(destination):
    root = ElementTree.parse(ROOT / "addon.xml").getroot()
    version = root.attrib["version"]
    archive_path = destination / "plugin.video.sharedgdrive-{0}.zip".format(version)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(ROOT / "addon.xml", "plugin.video.sharedgdrive/addon.xml")
    return archive_path


class RepositoryTests(unittest.TestCase):
    def test_builds_kodi_repository_site_for_custom_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = pathlib.Path(directory)
            site = REPOSITORY.build(temporary / "site", _addon_archive(temporary))

            self.assertEqual("k.atx.sx\n", (site / "CNAME").read_text(encoding="ascii"))
            self.assertTrue((site / ".nojekyll").is_file())
            self.assertTrue((site / "repository.sharedgdrive.zip").is_file())
            addon_version = ElementTree.parse(ROOT / "addon.xml").getroot().attrib["version"]
            addon_name = "plugin.video.sharedgdrive-{0}.zip".format(addon_version)
            self.assertTrue((site / "plugin.video.sharedgdrive" / addon_name).is_file())

            xml_bytes = (site / "addons.xml").read_bytes()
            expected_checksum = hashlib.md5(xml_bytes).hexdigest() + "\n"
            self.assertEqual(expected_checksum, (site / "addons.xml.md5").read_text(encoding="ascii"))
            addons = ElementTree.fromstring(xml_bytes)
            self.assertEqual(
                ["plugin.video.sharedgdrive", "repository.sharedgdrive"],
                [addon.attrib["id"] for addon in addons.findall("addon")],
            )

            repository = addons.find("./addon[@id='repository.sharedgdrive']")
            self.assertIsNotNone(repository)
            if repository is None:
                self.fail("repository add-on is missing")
            self.assertEqual("https://k.atx.sx/addons.xml", repository.findtext(".//info"))
            self.assertEqual("https://k.atx.sx/", repository.findtext(".//datadir"))
            self.assertEqual("false", repository.findtext(".//hashes"))

    def test_preserves_valid_historical_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = pathlib.Path(directory)
            current = _addon_archive(temporary)
            history = temporary / "history"
            history.mkdir()
            historical_addon = history / current.name
            shutil.copyfile(current, historical_addon)
            historical_repository = history / "repository.sharedgdrive-1.0.0.zip"
            site_without_history = REPOSITORY.build(temporary / "initial-site", current)
            shutil.copyfile(site_without_history / "repository.sharedgdrive.zip", historical_repository)

            site = REPOSITORY.build(temporary / "site", current, history)

            self.assertEqual(current.read_bytes(), (site / "plugin.video.sharedgdrive" / current.name).read_bytes())
            self.assertEqual(
                historical_repository.read_bytes(),
                (site / "repository.sharedgdrive" / historical_repository.name).read_bytes(),
            )

    def test_rejects_invalid_historical_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = pathlib.Path(directory)
            history = temporary / "history"
            history.mkdir()
            (history / "plugin.video.sharedgdrive-0.1.0.zip").write_bytes(b"not a zip")
            with self.assertRaises((RuntimeError, zipfile.BadZipFile)):
                REPOSITORY.build(temporary / "site", _addon_archive(temporary), history)

    def test_rejects_archive_that_does_not_match_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = pathlib.Path(directory)
            version = ElementTree.parse(ROOT / "addon.xml").getroot().attrib["version"]
            archive_path = temporary / "plugin.video.sharedgdrive-{0}.zip".format(version)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("wrong.id/addon.xml", "<addon id='wrong.id' version='{0}'/>".format(version))
            with self.assertRaises(RuntimeError):
                REPOSITORY.build(temporary / "site", archive_path)


if __name__ == "__main__":
    unittest.main()
