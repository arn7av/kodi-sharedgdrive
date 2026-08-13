import re
import urllib.parse

from .errors import AccessBoundaryError, ConfigurationError, DriveError, UnauthorizedError


_API_FILES = "https://www.googleapis.com/drive/v3/files"
_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_PAGES_PER_FOLDER = 10_000
_MAX_ITEMS_PER_FOLDER = 1_000_000
_MAX_FOLDERS_PER_EXPORT = 100_000
_MAX_EXPORT_DEPTH = 256


class DriveClient:
    def __init__(
        self,
        http,
        access_token,
        shared_drive_id,
        folder_cache=None,
        cache_fingerprint=None,
        token_refresher=None,
    ):
        self._http = http
        self._token = access_token
        self._drive_id = _validate_id(shared_drive_id, "shared drive")
        self._folder_cache = folder_cache
        self._cache_fingerprint = cache_fingerprint
        self._token_refresher = token_refresher

    @property
    def shared_drive_id(self):
        return self._drive_id

    def list_folder(self, folder_id):
        return self._list_folder(folder_id, validate_folder=True)

    def walk_videos(self, cancelled=None):
        pending = [(self._drive_id, [])]
        visited = set()
        while pending:
            if cancelled and cancelled():
                return
            folder_id, ancestors = pending.pop()
            if len(ancestors) > _MAX_EXPORT_DEPTH:
                raise DriveError("The shared drive folder hierarchy is too deep to export safely.")
            if folder_id in visited:
                continue
            visited.add(folder_id)
            if len(visited) > _MAX_FOLDERS_PER_EXPORT:
                raise DriveError("The shared drive has too many folders to export safely.")
            items = self._list_folder(folder_id, validate_folder=False, cancelled=cancelled)
            folders = []
            for item in items:
                if item.get("mimeType") == _FOLDER_MIME_TYPE:
                    folders.append(item)
                else:
                    yield ancestors, item
            for folder in reversed(folders):
                pending.append((folder["id"], ancestors + [folder]))

    def _list_folder(self, folder_id, validate_folder, cancelled=None):
        folder_id = _validate_id(folder_id, "folder")
        if validate_folder and self._folder_cache and self._cache_fingerprint:
            cached_items = self._folder_cache.get(self._cache_fingerprint, folder_id)
            if cached_items is not None:
                return self._validate_list_items(cached_items)

        if validate_folder and folder_id != self._drive_id:
            folder = self.get_item(folder_id)
            if folder.get("mimeType") != _FOLDER_MIME_TYPE:
                raise DriveError("The requested item is not a folder.")

        query = "'{0}' in parents and trashed=false and (mimeType='{1}' or mimeType contains 'video/')".format(
            folder_id,
            _FOLDER_MIME_TYPE,
        )
        parameters = {
            "corpora": "drive",
            "driveId": self._drive_id,
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
            "pageSize": "1000",
            "orderBy": "folder,name_natural",
            "q": query,
            "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,driveId,size,trashed,capabilities(canDownload))",
        }
        items = []
        page_count = 0
        seen_page_tokens = set()

        while True:
            if cancelled and cancelled():
                return items
            page_count += 1
            if page_count > _MAX_PAGES_PER_FOLDER:
                raise DriveError("A Google Drive folder has too many result pages.")
            response = self._get_json(_with_query(_API_FILES, parameters))
            incomplete_search = response.get("incompleteSearch")
            if incomplete_search is True:
                raise DriveError("Google Drive returned incomplete folder results; the operation was stopped safely.")
            if incomplete_search is not None and not isinstance(incomplete_search, bool):
                raise DriveError("Google Drive returned an invalid file list.")
            page_items = response.get("files", [])
            if not isinstance(page_items, list):
                raise DriveError("Google Drive returned an invalid file list.")

            items.extend(self._validate_list_items(page_items))
            if len(items) > _MAX_ITEMS_PER_FOLDER:
                raise DriveError("A Google Drive folder has too many playable items.")

            page_token = response.get("nextPageToken")
            if page_token is None:
                break
            if (
                not isinstance(page_token, str)
                or not page_token
                or len(page_token) > 4096
                or page_token in seen_page_tokens
            ):
                raise DriveError("Google Drive returned an invalid page token.")
            seen_page_tokens.add(page_token)
            parameters["pageToken"] = page_token

        if validate_folder and self._folder_cache and self._cache_fingerprint:
            self._folder_cache.put(self._cache_fingerprint, folder_id, items)
        return items

    def _validate_list_items(self, page_items):
        items = []
        for item in page_items:
            self._enforce_boundary(item)
            item_id = item.get("id")
            name = item.get("name")
            mime_type = item.get("mimeType")
            if (
                not isinstance(item_id, str)
                or not _ID_PATTERN.fullmatch(item_id)
                or not isinstance(name, str)
                or not isinstance(mime_type, str)
            ):
                raise DriveError("Google Drive returned invalid file metadata.")
            if item.get("trashed"):
                continue
            if mime_type == _FOLDER_MIME_TYPE:
                items.append(item)
                continue
            capabilities = item.get("capabilities")
            if _is_video(item) and isinstance(capabilities, dict) and capabilities.get("canDownload") is True:
                items.append(item)
        return items

    def get_playback_url(self, file_id, preflight=False):
        item = self.get_item(file_id)
        if item.get("mimeType") == _FOLDER_MIME_TYPE or not _is_video(item):
            raise DriveError("The requested item is not a playable video.")
        capabilities = item.get("capabilities")
        if not isinstance(capabilities, dict) or capabilities.get("canDownload") is not True:
            raise DriveError("Downloading this video is not permitted.")

        url = self._media_url(file_id)
        if preflight:
            self._probe_media(url)
        authorization = urllib.parse.quote("Bearer {0}".format(self._token), safe="")
        return "{0}|Authorization={1}".format(url, authorization), item

    def get_item(self, item_id):
        item_id = _validate_id(item_id, "item")
        parameters = {
            "supportsAllDrives": "true",
            "fields": "id,name,mimeType,driveId,size,trashed,capabilities(canDownload)",
        }
        item = self._get_json(_with_query("{0}/{1}".format(_API_FILES, item_id), parameters))
        self._enforce_boundary(item)
        if (
            not isinstance(item.get("id"), str)
            or not _ID_PATTERN.fullmatch(item["id"])
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("mimeType"), str)
        ):
            raise DriveError("Google Drive returned invalid file metadata.")
        if item.get("trashed"):
            raise DriveError("The requested Google Drive item is in the trash.")
        return item

    def _get_json(self, url):
        try:
            return self._http.get_json(url, headers=self._authorization_header())
        except UnauthorizedError:
            if not self._token_refresher:
                raise
            self._token = self._token_refresher()
            return self._http.get_json(url, headers=self._authorization_header())

    def _probe_media(self, url):
        try:
            self._http.probe_media(url, headers=self._authorization_header())
        except UnauthorizedError:
            if not self._token_refresher:
                raise
            self._token = self._token_refresher()
            self._http.probe_media(url, headers=self._authorization_header())

    @staticmethod
    def _media_url(file_id):
        return _with_query(
            "{0}/{1}".format(_API_FILES, _validate_id(file_id, "file")),
            {"alt": "media", "supportsAllDrives": "true"},
        )

    def _authorization_header(self):
        return {"Authorization": "Bearer {0}".format(self._token)}

    def _enforce_boundary(self, item):
        if not isinstance(item, dict) or item.get("driveId") != self._drive_id:
            raise AccessBoundaryError("The requested item is outside the configured shared drive.")


def _validate_id(value, label):
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise ConfigurationError("The configured {0} ID is invalid.".format(label))
    return value


def _is_video(item):
    mime_type = item.get("mimeType", "")
    return isinstance(mime_type, str) and mime_type.startswith("video/")


def _with_query(url, parameters):
    return "{0}?{1}".format(url, urllib.parse.urlencode(parameters))


FOLDER_MIME_TYPE = _FOLDER_MIME_TYPE
