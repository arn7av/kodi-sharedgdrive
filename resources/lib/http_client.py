import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from .errors import AuthenticationError, DriveError, UnauthorizedError


_ALLOWED_HOSTS = frozenset(("oauth2.googleapis.com", "www.googleapis.com"))
_RETRYABLE_STATUS = frozenset((429, 500, 502, 503, 504))
_MAX_JSON_BYTES = 8 * 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpClient:
    def __init__(self, timeout=20, attempts=3, sleep=time.sleep, opener=None):
        self._timeout = timeout
        self._attempts = attempts
        self._sleep = sleep
        self._opener = opener or urllib.request.build_opener(_NoRedirectHandler())

    def get_json(self, url, headers=None):
        return self._request_json("GET", url, headers=headers)

    def post_form(self, url, fields):
        body = urllib.parse.urlencode(fields).encode("ascii")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        return self._request_json("POST", url, headers=headers, body=body, auth_request=True)

    def _request_json(self, method, url, headers=None, body=None, auth_request=False):
        self._validate_url(url)
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)

        for attempt in range(self._attempts):
            try:
                with self._opener.open(request, timeout=self._timeout) as response:
                    payload = response.read(_MAX_JSON_BYTES + 1)
                if len(payload) > _MAX_JSON_BYTES:
                    raise DriveError("Google returned an unexpectedly large response.")
                document = json.loads(payload.decode("utf-8"))
                if not isinstance(document, dict):
                    raise DriveError("Google returned an invalid response.")
                return document
            except urllib.error.HTTPError as exc:
                if exc.code in _RETRYABLE_STATUS and attempt + 1 < self._attempts:
                    self._sleep(2**attempt)
                    continue
                if auth_request:
                    raise AuthenticationError("Google rejected the service-account credentials.") from exc
                if exc.code == 401:
                    raise UnauthorizedError("The Google access token was rejected.") from exc
                if exc.code == 403:
                    raise DriveError("Google Drive denied access to the requested resource.") from exc
                if exc.code == 404:
                    raise DriveError("The requested Google Drive resource was not found.") from exc
                raise DriveError("Google Drive returned HTTP status {0}.".format(exc.code)) from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                if attempt + 1 < self._attempts:
                    self._sleep(2**attempt)
                    continue
                error_type = AuthenticationError if auth_request else DriveError
                raise error_type("The connection to Google timed out or failed.") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                error_type = AuthenticationError if auth_request else DriveError
                raise error_type("Google returned an invalid response.") from exc

        raise DriveError("The request to Google failed.")

    @staticmethod
    def _validate_url(url):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
            raise ValueError("Refusing a request to a non-allowlisted URL.")
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("Refusing a URL containing credentials or a non-standard port.")
