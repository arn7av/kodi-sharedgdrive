import hashlib
import json
import os
import tempfile

from .constants import (
    FOLDER_CACHE_MAX_BYTES,
    FOLDER_CACHE_MAX_ENTRIES,
    FOLDER_CACHE_TTL_SECONDS,
)
from .validation import is_finite_number


_CACHE_FILENAME = "folder_results.json"
_CACHE_VERSION = 1


class FolderResultCache:
    def __init__(self, profile_path, clock):
        self._profile_path = profile_path
        self._clock = clock
        self._path = os.path.join(profile_path, _CACHE_FILENAME)

    @staticmethod
    def fingerprint(client_email, private_key, shared_drive_id):
        value = "\0".join((client_email, private_key, shared_drive_id)).encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    def get(self, fingerprint, folder_id):
        document = self._read(fingerprint)
        entry = document["entries"].get(folder_id)
        if not isinstance(entry, dict):
            return None
        cached_at = entry.get("cached_at")
        items = entry.get("items")
        now = self._clock()
        if (
            not is_finite_number(cached_at)
            or cached_at > now + 5
            or now - cached_at > FOLDER_CACHE_TTL_SECONDS
            or not isinstance(items, list)
            or not all(isinstance(item, dict) for item in items)
        ):
            return None

        return items

    def put(self, fingerprint, folder_id, items):
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            return
        document = self._read(fingerprint)
        now = self._clock()
        self._prune_expired(document, now)
        document["entries"][folder_id] = {
            "cached_at": now,
            "items": items,
        }
        try:
            self._enforce_bounds(document, protected_folder_id=folder_id)
            self._write(document)
        except (OSError, TypeError, ValueError):
            pass

    def clear(self):
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass

    def _read(self, fingerprint):
        empty = {"version": _CACHE_VERSION, "fingerprint": fingerprint, "entries": {}}
        try:
            with open(self._path, "r", encoding="utf-8") as source:
                raw = source.read(FOLDER_CACHE_MAX_BYTES + 1)
            if len(raw) > FOLDER_CACHE_MAX_BYTES:
                return empty
            document = json.loads(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return empty

        if (
            not isinstance(document, dict)
            or document.get("version") != _CACHE_VERSION
            or document.get("fingerprint") != fingerprint
            or not isinstance(document.get("entries"), dict)
        ):
            return empty
        return document

    def _prune_expired(self, document, now):
        expired = []
        for folder_id, entry in document["entries"].items():
            cached_at = entry.get("cached_at") if isinstance(entry, dict) else None
            if not is_finite_number(cached_at) or now - cached_at > FOLDER_CACHE_TTL_SECONDS:
                expired.append(folder_id)
        for folder_id in expired:
            del document["entries"][folder_id]

    def _enforce_bounds(self, document, protected_folder_id):
        entries = document["entries"]
        while len(entries) > FOLDER_CACHE_MAX_ENTRIES:
            self._remove_oldest(entries, protected_folder_id)

        while len(self._encode(document)) > FOLDER_CACHE_MAX_BYTES and entries:
            if len(entries) == 1 and protected_folder_id in entries:
                del entries[protected_folder_id]
                break
            self._remove_oldest(entries, protected_folder_id)

    @staticmethod
    def _remove_oldest(entries, protected_folder_id):
        candidates = [
            (entry.get("cached_at", 0) if isinstance(entry, dict) else 0, folder_id)
            for folder_id, entry in entries.items()
            if folder_id != protected_folder_id
        ]
        if not candidates:
            entries.pop(protected_folder_id, None)
            return
        del entries[min(candidates)[1]]

    def _write(self, document):
        encoded = self._encode(document)
        if len(encoded) > FOLDER_CACHE_MAX_BYTES:
            return
        os.makedirs(self._profile_path, mode=0o700, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".folder_results.",
            suffix=".tmp",
            dir=self._profile_path,
        )
        try:
            try:
                os.fchmod(descriptor, 0o600)
            except (AttributeError, OSError):
                pass
            with os.fdopen(descriptor, "wb") as destination:
                destination.write(encoded)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary_path, self._path)
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        finally:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

    @staticmethod
    def _encode(document):
        return json.dumps(
            document,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")

