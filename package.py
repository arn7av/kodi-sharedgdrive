import pathlib
import re
import xml.etree.ElementTree as ElementTree
import zipfile


ROOT = pathlib.Path(__file__).resolve().parent
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


def main():
    version = _version()
    archive_path = ROOT.parent / "plugin.video.sharedgdrive-{0}.zip".format(version)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in _package_files():
            archive.write(path, "{0}/{1}".format(ROOT.name, path.relative_to(ROOT).as_posix()))
    print(archive_path)


if __name__ == "__main__":
    main()
