import sys
import urllib.parse

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from .auth import ServiceAccountTokenProvider
from .config import KodiConfig
from .drive import DriveClient, FOLDER_MIME_TYPE, parse_debug_file_reference
from .errors import AccessBoundaryError, PluginError
from .folder_cache import FolderResultCache
from .http_client import HttpClient
from .strm_exporter import SnapshotExporter, StaleExportManager
from .token_cache import TokenCache


_PLAYBACK_MINIMUM_TOKEN_SECONDS = 55 * 60
_EXPORT_MINIMUM_TOKEN_SECONDS = 55 * 60


class KodiPlugin:
    def __init__(self, argv):
        self._base_url = argv[0]
        self._handle = int(argv[1])
        query = argv[2][1:] if len(argv) > 2 and argv[2].startswith("?") else ""
        self._request_error = None
        if len(query) > 4096:
            self._request_error = "The add-on request is too large."
            parsed = {}
        else:
            try:
                parsed = urllib.parse.parse_qs(query, max_num_fields=10)
            except ValueError:
                self._request_error = "The add-on request is invalid."
                parsed = {}
        self._params = {key: values[-1] for key, values in parsed.items()}
        self._addon = xbmcaddon.Addon()
        self._config = KodiConfig(self._addon, xbmcvfs)

    def run(self):
        action = self._params.get("action", "browse")
        try:
            if self._request_error:
                raise PluginError(self._request_error)
            if action == "import_credentials":
                self._import_credentials()
            elif action == "clear_credentials":
                self._clear_credentials()
            elif action == "clear_folder_cache":
                self._clear_folder_cache()
            elif action == "set_export_folder":
                self._set_export_folder()
            elif action == "export_snapshot":
                self._export_snapshot()
            elif action == "review_stale_exports":
                self._review_stale_exports()
            elif action == "play":
                self._play()
            elif action == "debug_play":
                self._debug_play()
            elif action == "browse":
                self._browse()
            else:
                raise PluginError("The requested add-on action is invalid.")
        except PluginError as exc:
            self._finish_with_error(action, str(exc))
        except Exception:
            self._finish_with_error(action, "The operation failed unexpectedly.")

    def _browse(self):
        if not self._config.is_complete:
            xbmcgui.Dialog().ok(
                self._addon.getLocalizedString(30013),
                self._addon.getLocalizedString(30014),
            )
            self._addon.openSettings()
            xbmcplugin.endOfDirectory(self._handle, succeeded=False)
            return

        folder_id = self._params.get("folder_id", self._config.shared_drive_id)
        drive = self._drive_client()
        items = drive.list_folder(folder_id)

        xbmcplugin.setContent(self._handle, "videos")
        directory_items = []
        for item in items:
            is_folder = item.get("mimeType") == FOLDER_MIME_TYPE
            action = "browse" if is_folder else "play"
            parameter = "folder_id" if is_folder else "file_id"
            url = self._plugin_url(action=action, **{parameter: item["id"]})
            list_item = xbmcgui.ListItem(label=item.get("name", ""))
            if is_folder:
                list_item.setArt({"icon": "DefaultFolder.png"})
            else:
                list_item.setProperty("IsPlayable", "true")
                list_item.setMimeType(item.get("mimeType", "video/*"))
                size = item.get("size")
                if isinstance(size, str) and size.isdigit():
                    list_item.setProperty("Size", size)
            directory_items.append((url, list_item, is_folder))

        xbmcplugin.addDirectoryItems(self._handle, directory_items, len(directory_items))
        xbmcplugin.addSortMethod(self._handle, xbmcplugin.SORT_METHOD_LABEL_IGNORE_THE)
        xbmcplugin.endOfDirectory(self._handle, succeeded=True, cacheToDisc=False)

    def _play(self):
        if not self._config.is_complete:
            raise PluginError("The add-on is not configured.")
        file_id = self._params.get("file_id", "")
        playback_url, item = self._drive_client(
            minimum_token_remaining=_PLAYBACK_MINIMUM_TOKEN_SECONDS
        ).get_playback_url(
            file_id,
            preflight=self._config.playback_preflight_enabled,
        )
        list_item = xbmcgui.ListItem(label=item.get("name", ""), path=playback_url)
        list_item.setProperty("IsPlayable", "true")
        list_item.setMimeType(item.get("mimeType", "video/*"))
        xbmcplugin.setResolvedUrl(self._handle, True, list_item)

    def _debug_play(self):
        if not self._config.is_complete:
            raise PluginError("The add-on is not configured.")
        reference = xbmcgui.Dialog().input(
            self._addon.getLocalizedString(30058),
            type=xbmcgui.INPUT_ALPHANUM,
        )
        if not reference:
            return

        file_id = parse_debug_file_reference(reference)
        drive = self._drive_client(
            use_folder_cache=False,
            minimum_token_remaining=_PLAYBACK_MINIMUM_TOKEN_SECONDS,
        )
        try:
            playback_url, item = drive.get_debug_playback_url(
                file_id,
                preflight=self._config.playback_preflight_enabled,
            )
        except AccessBoundaryError:
            if not xbmcgui.Dialog().yesno(
                self._addon.getLocalizedString(30059),
                self._addon.getLocalizedString(30060),
            ):
                return
            drive = self._drive_client(
                use_folder_cache=False,
                minimum_token_remaining=_PLAYBACK_MINIMUM_TOKEN_SECONDS,
            )
            playback_url, item = drive.get_debug_playback_url(
                file_id,
                allow_outside=True,
                preflight=self._config.playback_preflight_enabled,
            )
        list_item = xbmcgui.ListItem(label=item.get("name", ""), path=playback_url)
        list_item.setProperty("IsPlayable", "true")
        list_item.setMimeType(item.get("mimeType", "video/*"))
        xbmc.Player().play(playback_url, list_item)

    def _import_credentials(self):
        path = xbmcgui.Dialog().browseSingle(
            1,
            self._addon.getLocalizedString(30005),
            "files",
            ".json",
            False,
            False,
            "",
        )
        if self._config.import_credentials(path):
            self._clear_caches()
            xbmcgui.Dialog().notification(
                self._addon.getAddonInfo("name"),
                self._addon.getLocalizedString(30015),
                xbmcgui.NOTIFICATION_INFO,
            )

    def _clear_credentials(self):
        if not xbmcgui.Dialog().yesno(
            self._addon.getLocalizedString(30043),
            self._addon.getLocalizedString(30044),
        ):
            return
        self._config.clear_credentials()
        self._clear_caches()
        xbmcgui.Dialog().notification(
            self._addon.getAddonInfo("name"),
            self._addon.getLocalizedString(30016),
            xbmcgui.NOTIFICATION_INFO,
        )

    def _clear_folder_cache(self):
        self._folder_cache().clear()
        xbmcgui.Dialog().notification(
            self._addon.getAddonInfo("name"),
            self._addon.getLocalizedString(30036),
            xbmcgui.NOTIFICATION_INFO,
        )

    def _set_export_folder(self):
        path = xbmcgui.Dialog().browseSingle(
            3,
            self._addon.getLocalizedString(30024),
            "files",
            "",
            False,
            False,
            self._config.snapshot_export_folder,
        )
        if path:
            self._config.snapshot_export_folder = path

    def _export_snapshot(self):
        if not self._config.snapshot_export_enabled:
            raise PluginError("Snapshot export is disabled in the add-on settings.")
        if not self._config.is_complete:
            raise PluginError("The add-on is not configured.")
        if not self._config.snapshot_export_folder:
            raise PluginError("Select a snapshot export folder first.")

        auto_prune = self._config.snapshot_auto_prune
        confirmation_message = self._addon.getLocalizedString(
            30053 if auto_prune else 30046
        )
        if not xbmcgui.Dialog().yesno(
            self._addon.getLocalizedString(30045),
            confirmation_message,
        ):
            return

        progress = xbmcgui.DialogProgress()
        progress.create(self._addon.getLocalizedString(30030), "")
        try:
            exporter = SnapshotExporter(
                self._drive_client(
                    use_folder_cache=False,
                    minimum_token_remaining=_EXPORT_MINIMUM_TOKEN_SECONDS,
                ),
                xbmcvfs,
                self._config.snapshot_export_folder,
                "plugin://plugin.video.sharedgdrive/",
            )
            result = exporter.export(
                progress=lambda count, name: progress.update(
                    0,
                    self._addon.getLocalizedString(30031).format(count, name),
                ),
                cancelled=progress.iscanceled,
                auto_prune=auto_prune,
                prune_progress=lambda current, total, path: progress.update(
                    int(current * 100 / max(total, 1)),
                    self._addon.getLocalizedString(30054).format(current, total, path),
                ),
            )
        finally:
            progress.close()

        if result.get("cancelled"):
            message = self._addon.getLocalizedString(30032)
        elif auto_prune:
            result_string = 30055 if result.get("cleanup_cancelled") else 30052
            message = self._addon.getLocalizedString(result_string).format(
                result["written"],
                result["skipped"],
                result.get("auto_pruned", 0),
                result["stale"],
                result.get("cleanup_skipped", 0),
            )
        else:
            message = self._addon.getLocalizedString(30033).format(
                result["written"],
                result["skipped"],
                result["stale"],
            )
        xbmcgui.Dialog().ok(self._addon.getLocalizedString(30030), message)

    def _review_stale_exports(self):
        if not self._config.snapshot_export_enabled:
            raise PluginError("Snapshot export is disabled in the add-on settings.")
        if not self._config.shared_drive_id or not self._config.snapshot_export_folder:
            raise PluginError("Configure the shared drive and snapshot export folder first.")

        manager = StaleExportManager(
            xbmcvfs,
            self._config.snapshot_export_folder,
            "plugin://plugin.video.sharedgdrive/",
            self._config.shared_drive_id,
        )
        stale_paths = manager.list_owned_stale()
        if not stale_paths:
            xbmcgui.Dialog().ok(
                self._addon.getLocalizedString(30037),
                self._addon.getLocalizedString(30038),
            )
            return

        selected = xbmcgui.Dialog().multiselect(
            self._addon.getLocalizedString(30037),
            stale_paths,
        )
        if not selected:
            return
        selected_paths = [stale_paths[index] for index in selected if 0 <= index < len(stale_paths)]
        if not selected_paths:
            return
        if not xbmcgui.Dialog().yesno(
            self._addon.getLocalizedString(30039),
            self._addon.getLocalizedString(30040).format(len(selected_paths)),
        ):
            return

        progress = xbmcgui.DialogProgress()
        progress.create(self._addon.getLocalizedString(30037), "")
        try:
            result = manager.remove(
                selected_paths,
                progress=lambda current, total, path: progress.update(
                    int(current * 100 / max(total, 1)),
                    path,
                ),
                cancelled=progress.iscanceled,
            )
        finally:
            progress.close()
        xbmcgui.Dialog().ok(
            self._addon.getLocalizedString(30037),
            self._addon.getLocalizedString(30041).format(result["removed"], result["skipped"]),
        )

    def _profile_path(self):
        return xbmcvfs.translatePath(self._addon.getAddonInfo("profile"))

    def _token_cache(self):
        import time

        return TokenCache(self._profile_path(), time.time)

    def _folder_cache(self):
        import time

        return FolderResultCache(self._profile_path(), time.time)

    def _clear_caches(self):
        self._token_cache().clear()
        self._folder_cache().clear()

    def _drive_client(self, use_folder_cache=True, minimum_token_remaining=300):
        http = HttpClient()
        token_cache = self._token_cache()
        provider = ServiceAccountTokenProvider(
            http,
            self._config.client_email,
            self._config.private_key,
            cache=token_cache,
        )
        token = provider.get_token(minimum_remaining=minimum_token_remaining)

        def refresh_token():
            return provider.get_token(
                minimum_remaining=minimum_token_remaining,
                force_refresh=True,
            )
        folder_cache = self._folder_cache() if use_folder_cache else None
        cache_fingerprint = None
        if folder_cache:
            cache_fingerprint = folder_cache.fingerprint(
                self._config.client_email,
                self._config.private_key,
                self._config.shared_drive_id,
            )
        return DriveClient(
            http,
            token,
            self._config.shared_drive_id,
            folder_cache=folder_cache,
            cache_fingerprint=cache_fingerprint,
            token_refresher=refresh_token,
        )

    def _plugin_url(self, **parameters):
        return "{0}?{1}".format(self._base_url, urllib.parse.urlencode(parameters))

    def _finish_with_error(self, action, message):
        self._show_error(message)
        if action == "play":
            xbmcplugin.setResolvedUrl(self._handle, False, xbmcgui.ListItem())
        elif action == "browse":
            xbmcplugin.endOfDirectory(self._handle, succeeded=False)

    def _show_error(self, message):
        xbmcgui.Dialog().ok(self._addon.getLocalizedString(30019), message)


if __name__ == "__main__":
    KodiPlugin(sys.argv).run()
