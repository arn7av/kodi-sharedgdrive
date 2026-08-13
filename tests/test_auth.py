import base64
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resources.lib.auth import DRIVE_READONLY_SCOPE, TOKEN_URL, ServiceAccountTokenProvider
from resources.lib.errors import AuthenticationError


class FakeHttp:
    def __init__(self, response=None):
        self.url = None
        self.fields = None
        self.response = response or {"access_token": "short-lived-token", "expires_in": 3600}

    def post_form(self, url, fields):
        self.url = url
        self.fields = fields
        return self.response


class FakeRSA:
    @staticmethod
    def import_key(value):
        return value


class FakeSHA256:
    @staticmethod
    def new(value):
        return value


class FakeSigner:
    def sign(self, value):
        return b"test-signature"


class FakePkcs1:
    @staticmethod
    def new(key):
        return FakeSigner()


def _decode_segment(segment):
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))


class FakeCache:
    def __init__(self, token=None):
        self.token = token
        self.saved = None

    def credential_fingerprint(self, client_email, private_key, scope):
        return "fingerprint"

    def load(self, fingerprint, minimum_remaining=300):
        return self.token

    def save(self, fingerprint, token, expires_at):
        self.saved = (fingerprint, token, expires_at)


class AuthTests(unittest.TestCase):
    def test_creates_expected_jwt_claims_and_form_encoded_grant(self):
        modules = {
            "Cryptodome": types.ModuleType("Cryptodome"),
            "Cryptodome.Hash": types.ModuleType("Cryptodome.Hash"),
            "Cryptodome.PublicKey": types.ModuleType("Cryptodome.PublicKey"),
            "Cryptodome.Signature": types.ModuleType("Cryptodome.Signature"),
        }
        modules["Cryptodome.Hash"].SHA256 = FakeSHA256
        modules["Cryptodome.PublicKey"].RSA = FakeRSA
        modules["Cryptodome.Signature"].pkcs1_15 = FakePkcs1
        http = FakeHttp()
        cache = FakeCache()

        with mock.patch.dict(sys.modules, modules):
            provider = ServiceAccountTokenProvider(
                http,
                "viewer@example.iam.gserviceaccount.com",
                "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n",
                clock=lambda: 1_700_000_000,
                cache=cache,
            )
            token = provider.get_token()

        self.assertEqual("short-lived-token", token)
        self.assertEqual(TOKEN_URL, http.url)
        self.assertEqual(("fingerprint", "short-lived-token", 1_700_003_600), cache.saved)
        self.assertEqual(
            "urn:ietf:params:oauth:grant-type:jwt-bearer",
            http.fields["grant_type"],
        )
        header_segment, claim_segment, signature_segment = http.fields["assertion"].split(".")
        self.assertEqual({"alg": "RS256", "typ": "JWT"}, _decode_segment(header_segment))
        self.assertEqual(
            {
                "aud": TOKEN_URL,
                "exp": 1_700_003_600,
                "iat": 1_700_000_000,
                "iss": "viewer@example.iam.gserviceaccount.com",
                "scope": DRIVE_READONLY_SCOPE,
            },
            _decode_segment(claim_segment),
        )
        self.assertEqual(
            b"test-signature",
            base64.urlsafe_b64decode(signature_segment + "=" * (-len(signature_segment) % 4)),
        )

    def test_rejects_non_finite_or_boolean_token_lifetime(self):
        modules = {
            "Cryptodome": types.ModuleType("Cryptodome"),
            "Cryptodome.Hash": types.ModuleType("Cryptodome.Hash"),
            "Cryptodome.PublicKey": types.ModuleType("Cryptodome.PublicKey"),
            "Cryptodome.Signature": types.ModuleType("Cryptodome.Signature"),
        }
        modules["Cryptodome.Hash"].SHA256 = FakeSHA256
        modules["Cryptodome.PublicKey"].RSA = FakeRSA
        modules["Cryptodome.Signature"].pkcs1_15 = FakePkcs1
        for expires_in in (float("nan"), float("inf"), True, 10**1000):
            http = FakeHttp({"access_token": "token", "expires_in": expires_in})
            with self.subTest(expires_in=expires_in), mock.patch.dict(sys.modules, modules):
                provider = ServiceAccountTokenProvider(
                    http,
                    "viewer@example.iam.gserviceaccount.com",
                    "private-key",
                )
                with self.assertRaises(AuthenticationError):
                    provider.get_token()

    def test_force_refresh_bypasses_cached_token(self):
        modules = {
            "Cryptodome": types.ModuleType("Cryptodome"),
            "Cryptodome.Hash": types.ModuleType("Cryptodome.Hash"),
            "Cryptodome.PublicKey": types.ModuleType("Cryptodome.PublicKey"),
            "Cryptodome.Signature": types.ModuleType("Cryptodome.Signature"),
        }
        modules["Cryptodome.Hash"].SHA256 = FakeSHA256
        modules["Cryptodome.PublicKey"].RSA = FakeRSA
        modules["Cryptodome.Signature"].pkcs1_15 = FakePkcs1
        http = FakeHttp()
        with mock.patch.dict(sys.modules, modules):
            token = ServiceAccountTokenProvider(
                http,
                "viewer@example.iam.gserviceaccount.com",
                "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n",
                cache=FakeCache("rejected-cache-token"),
            ).get_token(force_refresh=True)

        self.assertEqual("short-lived-token", token)
        self.assertEqual(TOKEN_URL, http.url)

    def test_returns_cached_token_without_signing_or_network(self):
        http = FakeHttp()
        provider = ServiceAccountTokenProvider(
            http,
            "viewer@example.iam.gserviceaccount.com",
            "private-key",
            cache=FakeCache("cached-token"),
        )

        self.assertEqual("cached-token", provider.get_token())
        self.assertIsNone(http.url)


if __name__ == "__main__":
    unittest.main()
