import argparse
import hashlib
import html
import pathlib
import re
import shutil
import xml.etree.ElementTree as ElementTree
import zipfile


ROOT = pathlib.Path(__file__).resolve().parent
ADDON_ID = "plugin.video.sharedgdrive"
REPOSITORY_ID = "repository.sharedgdrive"
ADDON_MANIFEST = ROOT / "addon.xml"
REPOSITORY_SOURCE = ROOT / REPOSITORY_ID
REPOSITORY_MANIFEST = REPOSITORY_SOURCE / "addon.xml"
CUSTOM_DOMAIN = "k.atx.sx"
BASE_URL = "https://{0}/".format(CUSTOM_DOMAIN)
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _manifest(path, expected_id):
    root = ElementTree.parse(path).getroot()
    addon_id = root.attrib.get("id")
    version = root.attrib.get("version")
    if addon_id != expected_id:
        raise RuntimeError("{0} must declare add-on id {1}".format(path, expected_id))
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise RuntimeError("{0} contains an invalid version".format(path))
    return root, version


def _validate_repository_urls(root):
    extension = root.find("./extension[@point='xbmc.addon.repository']")
    if extension is None:
        raise RuntimeError("repository manifest is missing xbmc.addon.repository")
    directory = extension.find("dir")
    if directory is None or directory.attrib.get("minversion") != "19.0.0":
        raise RuntimeError("repository manifest must target Kodi 19 or newer")
    expected = {
        "info": BASE_URL + "addons.xml",
        "checksum": BASE_URL + "addons.xml.md5",
        "datadir": BASE_URL,
        "hashes": "false",
    }
    for name, value in expected.items():
        element = directory.find(name)
        if element is None or element.text != value:
            raise RuntimeError("repository manifest has an invalid {0} value".format(name))
    info = directory.find("info")
    if info.attrib.get("compressed") != "false":
        raise RuntimeError("repository info must declare compressed=false")


def _archive_manifest(archive_path, expected_id, expected_version):
    expected_name = "{0}/addon.xml".format(expected_id)
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        if expected_name not in names:
            raise RuntimeError("archive does not contain {0}".format(expected_name))
        if any(name.startswith("/") or ".." in pathlib.PurePosixPath(name).parts for name in names):
            raise RuntimeError("archive contains an unsafe path")
        root = ElementTree.fromstring(archive.read(expected_name))
    if root.attrib.get("id") != expected_id or root.attrib.get("version") != expected_version:
        raise RuntimeError("archive manifest does not match its expected id and version")


def _write_repository_archive(output, version):
    archive_path = output / REPOSITORY_ID / "{0}-{1}.zip".format(REPOSITORY_ID, version)
    archive_path.parent.mkdir(parents=True)
    info = zipfile.ZipInfo("{0}/addon.xml".format(REPOSITORY_ID), ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, REPOSITORY_MANIFEST.read_bytes())
    _archive_manifest(archive_path, REPOSITORY_ID, version)
    shutil.copyfile(archive_path, output / "{0}.zip".format(REPOSITORY_ID))
    return archive_path


def _copy_historical_archives(output, archive_directory, current_archives):
    if archive_directory is None:
        return
    archive_directory = pathlib.Path(archive_directory).resolve()
    if not archive_directory.is_dir():
        raise RuntimeError("historical archive directory does not exist")
    patterns = {
        ADDON_ID: re.compile(r"plugin\.video\.sharedgdrive-(\d+\.\d+\.\d+)\.zip"),
        REPOSITORY_ID: re.compile(r"repository\.sharedgdrive-(\d+\.\d+\.\d+)\.zip"),
    }
    for source in sorted(archive_directory.iterdir()):
        if not source.is_file() or source.is_symlink():
            continue
        for addon_id, pattern in patterns.items():
            match = pattern.fullmatch(source.name)
            if match is None:
                continue
            version = match.group(1)
            _archive_manifest(source, addon_id, version)
            destination = output / addon_id / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            current = current_archives.get((addon_id, version))
            if current is not None:
                if source.read_bytes() != current.read_bytes():
                    raise RuntimeError("published archive differs from the current build: {0}".format(source.name))
            elif destination.exists():
                if source.read_bytes() != destination.read_bytes():
                    raise RuntimeError("conflicting historical archive: {0}".format(source.name))
            else:
                shutil.copyfile(source, destination)
            break


def _write_addons_xml(output, manifests):
    document = ElementTree.Element("addons")
    for manifest in manifests:
        document.append(ElementTree.fromstring(ElementTree.tostring(manifest, encoding="utf-8")))
    ElementTree.indent(document, space="    ")
    xml_bytes = ElementTree.tostring(document, encoding="utf-8", xml_declaration=True) + b"\n"
    (output / "addons.xml").write_bytes(xml_bytes)
    (output / "addons.xml.md5").write_text(hashlib.md5(xml_bytes).hexdigest() + "\n", encoding="ascii")


def _write_index(output, addon_version, repository_version):
    addon_archive = "{0}/{0}-{1}.zip".format(ADDON_ID, addon_version)
    repository_archive = "{0}.zip".format(REPOSITORY_ID)
    page = """<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Shared Google Drive for Kodi</title>
</head>
<body>
    <main>
        <h1>Shared Google Drive for Kodi</h1>
        <p>Install the repository ZIP in Kodi once, then install Shared Google Drive from that repository to receive updates.</p>
        <p><a href="{repository_archive}">Download Shared Google Drive Repository {repository_version}</a></p>
        <p><a href="{addon_archive}">Download Shared Google Drive {addon_version} directly</a></p>
        <p><a href="https://github.com/arn7av/kodi-sharedgdrive">Source code and documentation</a></p>
    </main>
</body>
</html>
""".format(
        repository_archive=html.escape(repository_archive),
        repository_version=html.escape(repository_version),
        addon_archive=html.escape(addon_archive),
        addon_version=html.escape(addon_version),
    )
    (output / "index.html").write_text(page, encoding="utf-8")


def build(output, addon_archive, historical_directory=None):
    addon_root, addon_version = _manifest(ADDON_MANIFEST, ADDON_ID)
    repository_root, repository_version = _manifest(REPOSITORY_MANIFEST, REPOSITORY_ID)
    _validate_repository_urls(repository_root)

    addon_archive = pathlib.Path(addon_archive).resolve()
    expected_name = "{0}-{1}.zip".format(ADDON_ID, addon_version)
    if addon_archive.name != expected_name or not addon_archive.is_file():
        raise RuntimeError("expected add-on archive named {0}".format(expected_name))
    _archive_manifest(addon_archive, ADDON_ID, addon_version)

    output = pathlib.Path(output).resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    addon_directory = output / ADDON_ID
    addon_directory.mkdir()
    published_addon_archive = addon_directory / addon_archive.name
    shutil.copyfile(addon_archive, published_addon_archive)

    published_repository_archive = _write_repository_archive(output, repository_version)
    current_archives = {
        (ADDON_ID, addon_version): published_addon_archive,
        (REPOSITORY_ID, repository_version): published_repository_archive,
    }
    _copy_historical_archives(output, historical_directory, current_archives)
    _write_addons_xml(output, (addon_root, repository_root))
    _write_index(output, addon_version, repository_version)
    (output / "CNAME").write_text(CUSTOM_DOMAIN + "\n", encoding="ascii")
    (output / ".nojekyll").touch()
    return output


def main():
    parser = argparse.ArgumentParser(description="Build the static Kodi repository site")
    parser.add_argument("--addon-archive", required=True, help="installable video add-on ZIP")
    parser.add_argument("--output", default=str(ROOT.parent / "kodi-repository-site"), help="output directory")
    parser.add_argument("--historical-directory", help="directory of prior versioned release ZIPs to preserve")
    arguments = parser.parse_args()
    print(build(arguments.output, arguments.addon_archive, arguments.historical_directory))


if __name__ == "__main__":
    main()
