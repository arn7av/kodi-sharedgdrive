import base64
import json
import math
import time

from .errors import AuthenticationError


TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
JWT_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"


class ServiceAccountTokenProvider:
    def __init__(self, http, client_email, private_key, clock=time.time, cache=None):
        self._http = http
        self._client_email = client_email
        self._private_key = private_key
        self._clock = clock
        self._cache = cache

    def get_token(self, minimum_remaining=300, force_refresh=False):
        if not self._client_email or not self._private_key:
            raise AuthenticationError("Service-account credentials are not configured.")

        fingerprint = None
        if self._cache:
            fingerprint = self._cache.credential_fingerprint(
                self._client_email,
                self._private_key,
                DRIVE_READONLY_SCOPE,
            )
            if not force_refresh:
                cached_token = self._cache.load(fingerprint, minimum_remaining=minimum_remaining)
                if cached_token:
                    return cached_token

        assertion = self._create_assertion()
        response = self._http.post_form(
            TOKEN_URL,
            {"grant_type": JWT_GRANT_TYPE, "assertion": assertion},
        )
        token = response.get("access_token")
        expires_in = response.get("expires_in")
        if not isinstance(token, str) or not token:
            raise AuthenticationError("Google did not return an access token.")
        if (
            not isinstance(expires_in, (int, float))
            or isinstance(expires_in, bool)
            or (isinstance(expires_in, float) and not math.isfinite(expires_in))
            or expires_in <= 0
            or expires_in > 24 * 60 * 60
        ):
            raise AuthenticationError("Google did not return a valid token lifetime.")
        if self._cache and fingerprint:
            try:
                self._cache.save(fingerprint, token, self._clock() + expires_in)
            except OSError:
                pass
        return token

    def _create_assertion(self):
        try:
            from Cryptodome.Hash import SHA256
            from Cryptodome.PublicKey import RSA
            from Cryptodome.Signature import pkcs1_15
        except ImportError:
            try:
                from Crypto.Hash import SHA256
                from Crypto.PublicKey import RSA
                from Crypto.Signature import pkcs1_15
            except ImportError as exc:
                raise AuthenticationError("The PyCryptodome Kodi dependency is unavailable.") from exc

        issued_at = int(self._clock())
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {
            "iss": self._client_email,
            "scope": DRIVE_READONLY_SCOPE,
            "aud": TOKEN_URL,
            "iat": issued_at,
            "exp": issued_at + 3600,
        }
        signing_input = "{0}.{1}".format(
            _base64url(_compact_json(header)),
            _base64url(_compact_json(claims)),
        ).encode("ascii")

        try:
            key = RSA.import_key(self._private_key.encode("utf-8"))
            signature = pkcs1_15.new(key).sign(SHA256.new(signing_input))
        except (ValueError, IndexError, TypeError) as exc:
            raise AuthenticationError("The configured service-account private key is invalid.") from exc

        return "{0}.{1}".format(signing_input.decode("ascii"), _base64url(signature))


def _compact_json(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
