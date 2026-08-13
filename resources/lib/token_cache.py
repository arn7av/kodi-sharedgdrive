import hashlib
import json
import math
import os
import tempfile


_CACHE_FILENAME = "access_token.json"
_CACHE_VERSION = 1
_REFRESH_MARGIN_SECONDS = 300
_MAX_TOKEN_LIFETIME_SECONDS = 24 * 60 * 60


class TokenCache:
    def __init__(self, profile_path, clock):
        self._profile_path = profile_path
        self._clock = clock
        self._path = os.path.join(profile_path, _CACHE_FILENAME)

    @staticmethod
    def credential_fingerprint(client_email, private_key, scope):
        value = "\0".join((client_email, private_key, scope)).encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    def load(self, fingerprint, minimum_remaining=_REFRESH_MARGIN_SECONDS):
        try:
            with open(self._path, "r", encoding="utf-8") as source:
                raw = source.read(16 * 1024 + 1)
            if len(raw) > 16 * 1024:
                return None
            document = json.loads(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

        now = self._clock()
        if (
            not isinstance(document, dict)
            or document.get("version") != _CACHE_VERSION
            or document.get("fingerprint") != fingerprint
            or not isinstance(document.get("token"), str)
            or not document["token"]
            or not _is_finite_number(document.get("expires_at"))
            or document["expires_at"] <= now + max(_REFRESH_MARGIN_SECONDS, minimum_remaining)
            or document["expires_at"] > now + _MAX_TOKEN_LIFETIME_SECONDS
        ):
            return None
        return document["token"]

    def save(self, fingerprint, token, expires_at):
        now = self._clock()
        if (
            not token
            or not _is_finite_number(expires_at)
            or expires_at <= now + _REFRESH_MARGIN_SECONDS
            or expires_at > now + _MAX_TOKEN_LIFETIME_SECONDS
        ):
            return

        os.makedirs(self._profile_path, mode=0o700, exist_ok=True)
        document = {
            "version": _CACHE_VERSION,
            "fingerprint": fingerprint,
            "token": token,
            "expires_at": expires_at,
        }
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".access_token.",
            suffix=".tmp",
            dir=self._profile_path,
        )
        try:
            try:
                os.fchmod(descriptor, 0o600)
            except (AttributeError, OSError):
                pass
            with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
                json.dump(
                    document,
                    destination,
                    separators=(",", ":"),
                    sort_keys=True,
                    allow_nan=False,
                )
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

    def clear(self):
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass


def _is_finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or math.isfinite(value)
