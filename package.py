import pathlib
import re
import xml.etree.ElementTree as ElementTree
import zipfile


ROOT = pathlib.Path(__file__).resolve().parent
ADDON_ID = "plugin.video.sharedgdrive"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ALLOWED_PATHS = frozenset((
    "addon.py",
    "addon.xml",
    "DESIGN_REVIEW.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "resources/__init__.py",
    "resources/settings.xml",
    "resources/language/resource.language.en_gb/strings.po",
    "resources/lib/__init__.py",
    "resources/lib/auth.py",
    "resources/lib/config.py",
    "resources/lib/constants.py",
    "resources/lib/drive.py",
    "resources/lib/errors.py",
    "resources/lib/folder_cache.py",
    "resources/lib/http_client.py",
    "resources/lib/kodi_plugin.py",
    "resources/lib/strm_exporter.py",
    "resources/lib/token_cache.py",
    "resources/lib/validation.py",
))


def _version():
    root = ElementTree.parse(ROOT / "addon.xml").getroot()
    version = root.attrib.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError("addon.xml contains an invalid version")
    return version


def _package_files():
    files = []
    for relative_path in sorted(ALLOWED_PATHS):
        path = ROOT / relative_path
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("required package file is missing or is a symlink: {0}".format(relative_path))
        if any(parent.is_symlink() for parent in path.parents if parent != ROOT.parent):
            raise RuntimeError("package path has a symlinked parent: {0}".format(relative_path))
        files.append(path)
    return files


def _write_file(archive, path, archive_name):
    info = zipfile.ZipInfo(archive_name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, path.read_bytes())


def build(output_directory=None):
    version = _version()
    output = ROOT.parent if output_directory is None else pathlib.Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / "{0}-{1}.zip".format(ADDON_ID, version)
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in _package_files():
            archive_name = "{0}/{1}".format(ADDON_ID, path.relative_to(ROOT).as_posix())
            _write_file(archive, path, archive_name)
    return archive_path


def main():
    print(build())


if __name__ == "__main__":
    main()
