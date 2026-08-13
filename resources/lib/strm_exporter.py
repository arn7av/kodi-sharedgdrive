import json
import os
import re
import urllib.parse
import uuid

from .errors import ConfigurationError, DriveError


_MANIFEST_FILENAME = ".sharedgdrive-export.json"
_MANIFEST_VERSION = 2
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_MANIFEST_ENTRIES = 100_000
_MAX_STRM_BYTES = 8 * 1024
_CHECKPOINT_WRITES = 5_000
_STALE_REMOVAL_CHECKPOINT = 500
_MAX_REVIEWABLE_STALE = 5_000
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*%#\x00-\x1f]')
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class SnapshotExporter:
    def __init__(self, drive, vfs, destination, plugin_base_url):
        self._drive = drive
        self._vfs = vfs
        self._destination = _validate_destination(destination)
        self._plugin_base_url = plugin_base_url

        self._assigned_files = {}
        self._folder_segments = {}
        self._used_folder_segments = {}
        self._known_directories = {self._destination}
        self._manifest = _ManifestStore(
            vfs,
            self._destination,
            plugin_base_url,
            drive.shared_drive_id,
        )

    def export(
        self,
        progress=None,
        cancelled=None,
        auto_prune=False,
        prune_progress=None,
    ):
        if not self._vfs.exists(self._destination) and not self._vfs.mkdirs(self._destination):
            raise DriveError("The snapshot export folder could not be created.")

        old_manifest = self._manifest.load()
        if old_manifest.get("drive_id") == self._drive.shared_drive_id:
            owned_files = dict(old_manifest.get("files", {}))
            owned_files.update(old_manifest.get("stale_files", {}))
        else:
            owned_files = {}
        new_owned_files = {}
        recoverable_paths = set(owned_files)
        recoverable_manifest_bytes = self._manifest.active_manifest_size(owned_files)
        result = {"found": 0, "written": 0, "skipped": 0, "stale": 0}
        writes_since_checkpoint = 0

        try:
            for folders, item in self._drive.walk_videos(cancelled=cancelled):
                if cancelled and cancelled():
                    result["cancelled"] = True
                    break

                result["found"] += 1
                if progress:
                    progress(result["found"], item.get("name", ""))

                relative_directory = self._resolve_directory(folders)
                relative_path = self._resolve_file_path(relative_directory, item, owned_files)
                output_path = _join(self._destination, relative_path)
                owner = owned_files.get(relative_path)
                plugin_url = self._manifest.plugin_url(item["id"])
                is_new_ownership = relative_path not in recoverable_paths
                if is_new_ownership:
                    if len(recoverable_paths) >= _MAX_MANIFEST_ENTRIES:
                        raise DriveError("The snapshot export has reached its safe file limit.")
                    prospective_bytes = recoverable_manifest_bytes + self._manifest.entry_size(
                        relative_path,
                        item["id"],
                        include_comma=bool(recoverable_paths),
                    )
                    if prospective_bytes > _MAX_MANIFEST_BYTES:
                        raise DriveError("The snapshot export has reached its safe manifest-size limit.")

                if self._vfs.exists(output_path):
                    is_owned = owner == item["id"] and _has_expected_content(
                        self._vfs,
                        output_path,
                        plugin_url,
                    )
                    if not is_owned:
                        result["skipped"] += 1
                        continue
                    new_owned_files[relative_path] = item["id"]
                    result["skipped"] += 1
                    continue
                else:
                    result["written"] += 1

                self._ensure_directory(output_path.rsplit("/", 1)[0])
                self._write_strm(output_path, plugin_url)
                new_owned_files[relative_path] = item["id"]
                if is_new_ownership:
                    recoverable_paths.add(relative_path)
                    recoverable_manifest_bytes = prospective_bytes
                writes_since_checkpoint += 1
                if writes_since_checkpoint >= _CHECKPOINT_WRITES:
                    self._save_partial(owned_files, new_owned_files)
                    writes_since_checkpoint = 0
        except Exception:
            self._save_partial_best_effort(owned_files, new_owned_files)
            raise

        if cancelled and cancelled():
            result["cancelled"] = True

        if result.get("cancelled"):
            self._save_partial(owned_files, new_owned_files)
            return result

        stale_files = {}
        for relative_path, file_id in owned_files.items():
            if relative_path in new_owned_files:
                continue
            output_path = _join(self._destination, relative_path)
            if self._vfs.exists(output_path) and _has_expected_content(
                self._vfs,
                output_path,
                self._manifest.plugin_url(file_id),
            ):
                stale_files[relative_path] = file_id
        result["stale"] = len(stale_files)

        try:
            self._manifest.save({
                "version": _MANIFEST_VERSION,
                "drive_id": self._drive.shared_drive_id,
                "files": new_owned_files,
                "stale_files": stale_files,
            })
        except Exception:
            self._save_partial_best_effort(owned_files, new_owned_files)
            raise

        if auto_prune:
            cleanup = StaleExportManager(
                self._vfs,
                self._destination,
                self._plugin_base_url,
                self._drive.shared_drive_id,
            ).remove_all_owned_stale(
                progress=prune_progress,
                cancelled=cancelled,
            )
            result["auto_pruned"] = cleanup["removed"]
            result["cleanup_skipped"] = cleanup["skipped"]
            result["stale"] = cleanup["remaining"]
            if cleanup["cancelled"]:
                result["cleanup_cancelled"] = True
        return result

    def _save_partial(self, owned_files, new_owned_files):
        active = dict(owned_files)
        active.update(new_owned_files)
        self._manifest.save({
            "version": _MANIFEST_VERSION,
            "drive_id": self._drive.shared_drive_id,
            "files": active,
            "stale_files": {},
        })

    def _save_partial_best_effort(self, owned_files, new_owned_files):
        try:
            self._save_partial(owned_files, new_owned_files)
        except Exception:
            pass

    def _ensure_directory(self, path):
        if path in self._known_directories:
            return
        if not self._vfs.exists(path) and not self._vfs.mkdirs(path):
            raise DriveError("A snapshot export folder could not be created.")
        self._known_directories.add(path)

    def _write_strm(self, output_path, content):
        temporary_path = output_path + ".tmp." + uuid.uuid4().hex
        with self._vfs.File(temporary_path, "w") as destination:
            destination.write(content)
        try:
            if self._vfs.exists(output_path):
                raise DriveError("A snapshot file appeared during export and was not replaced.")
            if not self._vfs.rename(temporary_path, output_path):
                raise DriveError("A snapshot file could not be installed.")
        finally:
            if self._vfs.exists(temporary_path):
                self._vfs.delete(temporary_path)

    def _resolve_directory(self, folders):
        parent_key = ()
        segments = []
        for folder in folders:
            folder_key = parent_key + (folder["id"],)
            segment = self._folder_segments.get(folder_key)
            if not segment:
                base = _safe_name(folder.get("name", "Folder"))
                used = self._used_folder_segments.setdefault(parent_key, {})
                segment = _unique_name(base, folder["id"], used)
                used[segment.casefold()] = folder["id"]
                self._folder_segments[folder_key] = segment
            segments.append(segment)
            parent_key = folder_key
        return "/".join(segments)

    def _resolve_file_path(self, relative_directory, item, owned_files):
        filename = _safe_strm_name(item.get("name", "Video"))
        relative_path = _join(relative_directory, filename) if relative_directory else filename
        assigned_owner = self._assigned_files.get(relative_path.casefold())
        existing_owner = owned_files.get(relative_path)

        if assigned_owner not in (None, item["id"]) or existing_owner not in (None, item["id"]):
            stem = filename[:-5]
            filename = "{0} [{1}].strm".format(stem, item["id"][:8])
            relative_path = _join(relative_directory, filename) if relative_directory else filename

        if not _is_safe_relative_strm_path(relative_path):
            raise DriveError("A generated snapshot path is too long or unsafe.")
        self._assigned_files[relative_path.casefold()] = item["id"]
        return relative_path


class StaleExportManager:
    def __init__(self, vfs, destination, plugin_base_url, shared_drive_id):
        self._vfs = vfs
        self._destination = _validate_destination(destination)
        self._manifest = _ManifestStore(
            vfs,
            self._destination,
            plugin_base_url,
            shared_drive_id,
        )
        if not isinstance(shared_drive_id, str) or not _ID_PATTERN.fullmatch(shared_drive_id):
            raise ConfigurationError("The configured shared drive ID is invalid.")
        self._drive_id = shared_drive_id

    def list_owned_stale(self):
        document = self._manifest.load()
        if document.get("drive_id") != self._drive_id:
            return []
        results = []
        for relative_path, file_id in document.get("stale_files", {}).items():
            output_path = _join(self._destination, relative_path)
            if self._vfs.exists(output_path) and _has_expected_content(
                self._vfs,
                output_path,
                self._manifest.plugin_url(file_id),
            ):
                results.append(relative_path)
                if len(results) > _MAX_REVIEWABLE_STALE:
                    raise DriveError("There are too many stale files to review safely in one dialog.")
        return sorted(results, key=str.casefold)

    def remove(self, relative_paths, progress=None, cancelled=None):
        requested = set(relative_paths)
        document = self._manifest.load()
        if document.get("drive_id") != self._drive_id:
            return {"removed": 0, "skipped": len(requested)}
        result = self._remove(document, requested, progress, cancelled)
        return {"removed": result["removed"], "skipped": result["skipped"]}

    def remove_all_owned_stale(self, progress=None, cancelled=None):
        """Remove validated exporter-owned stale files without the review-dialog cap."""
        document = self._manifest.load()
        if document.get("drive_id") != self._drive_id:
            return {"removed": 0, "skipped": 0, "remaining": 0, "cancelled": False}
        return self._remove(
            document,
            tuple(document.get("stale_files", {})),
            progress,
            cancelled,
        )

    def _remove(self, document, requested, progress, cancelled):
        stale_files = dict(document.get("stale_files", {}))
        removed = 0
        skipped = 0
        was_cancelled = False
        active_folded = {path.casefold() for path in document.get("files", {})}
        for index, relative_path in enumerate(requested, start=1):
            if cancelled and cancelled():
                was_cancelled = True
                break
            if progress:
                progress(index, len(requested), relative_path)
            if relative_path.casefold() in active_folded:
                skipped += 1
                continue
            file_id = stale_files.get(relative_path)
            if not file_id:
                skipped += 1
                continue
            output_path = _join(self._destination, relative_path)
            if not self._vfs.exists(output_path) or not _has_expected_content(
                self._vfs,
                output_path,
                self._manifest.plugin_url(file_id),
            ):
                del stale_files[relative_path]
                skipped += 1
                continue
            if self._vfs.delete(output_path):
                del stale_files[relative_path]
                removed += 1
                if removed % _STALE_REMOVAL_CHECKPOINT == 0:
                    document["stale_files"] = stale_files
                    self._manifest.save(document)
            else:
                skipped += 1

        document["stale_files"] = stale_files
        self._manifest.save(document)
        return {
            "removed": removed,
            "skipped": skipped,
            "remaining": len(stale_files),
            "cancelled": was_cancelled,
        }


class _ManifestStore:
    def __init__(self, vfs, destination, plugin_base_url, shared_drive_id):
        self._vfs = vfs
        self._destination = destination
        self._plugin_base_url = plugin_base_url
        self._drive_id = shared_drive_id
        self._path = _join(destination, _MANIFEST_FILENAME)

    def active_manifest_size(self, entries):
        document = {
            "version": _MANIFEST_VERSION,
            "drive_id": self._drive_id,
            "files": entries,
            "stale_files": {},
        }
        return len(json.dumps(document, separators=(",", ":")).encode("utf-8"))

    @staticmethod
    def entry_size(relative_path, file_id, include_comma):
        encoded_key = json.dumps(relative_path, separators=(",", ":")).encode("utf-8")
        encoded_value = json.dumps(file_id, separators=(",", ":")).encode("utf-8")
        return len(encoded_key) + 1 + len(encoded_value) + (1 if include_comma else 0)

    def plugin_url(self, file_id):
        return "{0}?{1}\n".format(
            self._plugin_base_url,
            urllib.parse.urlencode({"action": "play", "file_id": file_id}),
        )

    def load(self):
        if not self._vfs.exists(self._path):
            return {}
        try:
            with self._vfs.File(self._path) as source:
                raw = _read_limited(source, _MAX_MANIFEST_BYTES)
            document = json.loads(raw)
        except DriveError:
            raise
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise DriveError("The snapshot export manifest is invalid.") from exc
        return self._validate(document)

    def save(self, document):
        document = self._validate(document)
        encoded = json.dumps(document, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_MANIFEST_BYTES:
            raise DriveError("The snapshot export manifest is too large.")
        if self._vfs.exists(self._path):
            try:
                with self._vfs.File(self._path) as source:
                    if _read_limited(source, _MAX_MANIFEST_BYTES) == encoded:
                        return
            except (OSError, ValueError, TypeError, DriveError):
                pass

        temporary_path = self._path + ".tmp." + uuid.uuid4().hex
        with self._vfs.File(temporary_path, "w") as destination:
            destination.write(encoded)
        try:
            if self._vfs.exists(self._path):
                _replace_with_rollback(
                    self._vfs,
                    temporary_path,
                    self._path,
                    "The snapshot export manifest could not be replaced.",
                )
            elif not self._vfs.rename(temporary_path, self._path):
                raise DriveError("The snapshot export manifest could not be installed.")
        finally:
            if self._vfs.exists(temporary_path):
                self._vfs.delete(temporary_path)

    def _validate(self, document):
        if not isinstance(document, dict):
            raise DriveError("The snapshot export manifest is invalid.")
        version = document.get("version")
        if version not in (1, _MANIFEST_VERSION):
            raise DriveError("The snapshot export manifest version is unsupported.")
        drive_id = document.get("drive_id")
        if not isinstance(drive_id, str) or not _ID_PATTERN.fullmatch(drive_id):
            raise DriveError("The snapshot export manifest has an invalid drive ID.")

        files = self._validate_entries(document.get("files"))
        stale_files = self._validate_entries(document.get("stale_files", {}))
        active_folded = {path.casefold() for path in files}
        stale_folded = {path.casefold() for path in stale_files}
        if len(active_folded) != len(files) or len(stale_folded) != len(stale_files) or active_folded & stale_folded:
            raise DriveError("The snapshot export manifest contains conflicting paths.")
        if len(files) + len(stale_files) > _MAX_MANIFEST_ENTRIES:
            raise DriveError("The snapshot export manifest has too many entries.")
        return {
            "version": _MANIFEST_VERSION,
            "drive_id": drive_id,
            "files": files,
            "stale_files": stale_files,
        }

    @staticmethod
    def _validate_entries(entries):
        if not isinstance(entries, dict):
            raise DriveError("The snapshot export manifest is invalid.")
        validated = {}
        for relative_path, file_id in entries.items():
            if not _is_safe_relative_strm_path(relative_path):
                raise DriveError("The snapshot export manifest contains an unsafe path.")
            if not isinstance(file_id, str) or not _ID_PATTERN.fullmatch(file_id):
                raise DriveError("The snapshot export manifest contains an invalid file ID.")
            validated[relative_path] = file_id
        return validated


def _replace_with_rollback(vfs, temporary_path, target_path, error_message):
    backup_path = target_path + ".backup." + uuid.uuid4().hex
    if not vfs.rename(target_path, backup_path):
        raise DriveError(error_message)
    installed = False
    try:
        if not vfs.rename(temporary_path, target_path):
            raise DriveError(error_message)
        installed = True
    finally:
        if installed:
            if vfs.exists(backup_path):
                vfs.delete(backup_path)
        elif vfs.exists(backup_path):
            if not vfs.rename(backup_path, target_path):
                raise DriveError("Replacement failed and the original file could not be restored.")


def _read_limited(source, limit):
    try:
        value = source.read(limit + 1)
    except TypeError:
        value = source.read()
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
        raise DriveError("A snapshot export file is too large.")
    return value


def _has_expected_content(vfs, path, expected):
    try:
        with vfs.File(path) as source:
            content = _read_limited(source, _MAX_STRM_BYTES)
        return content.strip() == expected.strip()
    except (OSError, ValueError, TypeError, DriveError):
        return False


def _is_safe_relative_strm_path(path):
    if not isinstance(path, str) or not path or len(path) > 4096:
        return False
    if (
        path.startswith(("/", "\\"))
        or "\\" in path
        or any(character in path for character in ("%", "#", "?"))
        or not path.lower().endswith(".strm")
    ):
        return False
    parts = path.split("/")
    return all(part not in ("", ".", "..") and len(part) <= 255 for part in parts)


def _validate_destination(destination):
    if not isinstance(destination, str) or not destination.strip():
        raise ConfigurationError("Select a snapshot export folder first.")
    destination = destination.strip().rstrip("/\\")
    parsed = urllib.parse.urlsplit(destination)
    if parsed.username or parsed.password:
        raise ConfigurationError("The snapshot export path must not contain embedded credentials.")
    return destination


def _safe_strm_name(name):
    stem, _ = os.path.splitext(name)
    return _safe_name(stem or name) + ".strm"


def _safe_name(name):
    cleaned = _INVALID_FILENAME.sub("_", str(name)).strip().rstrip(".")
    if cleaned.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }:
        cleaned = "_" + cleaned
    if cleaned in ("", ".", ".."):
        cleaned = "Unnamed"
    return cleaned[:180]


def _unique_name(base, item_id, used):
    if base.casefold() not in used:
        return base
    return "{0} [{1}]".format(base, item_id[:8])


def _join(base, child):
    if not base:
        return child
    return base.rstrip("/\\") + "/" + child.lstrip("/\\")
