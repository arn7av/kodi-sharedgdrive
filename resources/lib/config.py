import json
import urllib.parse

from .errors import ConfigurationError


class KodiConfig:
    def __init__(self, addon, xbmcvfs):
        self._addon = addon
        self._xbmcvfs = xbmcvfs

    @property
    def shared_drive_id(self):
        return self._addon.getSettingString("shared_drive_id").strip()

    @property
    def client_email(self):
        return self._addon.getSettingString("client_email").strip()

    @property
    def private_key(self):
        return self._addon.getSettingString("private_key")

    @property
    def is_complete(self):
        return bool(self.shared_drive_id and self.client_email and self.private_key)

    @property
    def playback_preflight_enabled(self):
        return self._addon.getSettingBool("playback_preflight_enabled")

    @property
    def snapshot_export_enabled(self):
        return self._addon.getSettingBool("snapshot_export_enabled")

    @property
    def snapshot_auto_prune(self):
        return self._addon.getSettingBool("snapshot_auto_prune")

    @property
    def snapshot_export_folder(self):
        value = self._addon.getSettingString("snapshot_export_folder").strip()
        parsed = urllib.parse.urlsplit(value)
        if parsed.username or parsed.password:
            self._addon.setSettingString("snapshot_export_folder", "")
            raise ConfigurationError("The stored snapshot export path contained embedded credentials and was cleared.")
        return value

    @snapshot_export_folder.setter
    def snapshot_export_folder(self, value):
        parsed = urllib.parse.urlsplit(value)
        if parsed.username or parsed.password:
            raise ConfigurationError("The snapshot export path must not contain embedded credentials.")
        self._addon.setSettingString("snapshot_export_folder", value)


    def import_credentials(self, path):
        if not path:
            return False

        try:
            with self._xbmcvfs.File(path) as source:
                try:
                    raw = source.read(256 * 1024 + 1)
                except TypeError:
                    raw = source.read()
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 256 * 1024:
                raise ConfigurationError("The selected credential file is too large.")
            document = json.loads(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ConfigurationError("The selected file is not valid JSON.") from exc

        email = document.get("client_email") if isinstance(document, dict) else None
        private_key = document.get("private_key") if isinstance(document, dict) else None
        credential_type = document.get("type") if isinstance(document, dict) else None
        if (
            credential_type != "service_account"
            or not isinstance(email, str)
            or "@" not in email
            or not isinstance(private_key, str)
            or not private_key.startswith("-----BEGIN PRIVATE KEY-----")
            or not private_key.rstrip().endswith("-----END PRIVATE KEY-----")
        ):
            raise ConfigurationError("The selected file is not a valid service-account credential.")

        self._addon.setSettingString("client_email", email.strip())
        self._addon.setSettingString("private_key", private_key)
        return True

    def clear_credentials(self):
        self._addon.setSettingString("client_email", "")
        self._addon.setSettingString("private_key", "")
