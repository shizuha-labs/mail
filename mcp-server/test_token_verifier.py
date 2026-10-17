import asyncio
import os
import sys
import time
import unittest
from unittest import mock

import jwt

sys.path.insert(0, os.path.dirname(__file__))
from base import (  # noqa: E402
    ShizuhaTokenVerifier,
    _oauth_issuer,
    _register_oauth_discovery_routes,
    _resource_server_url,
)


class _FakeSigningKey:
    key = "rs-key"


class _FakeJwksClient:
    urls = []

    def __init__(self, url):
        self.url = url
        self.urls.append(url)

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey()


class ShizuhaTokenVerifierTests(unittest.TestCase):
    def setUp(self):
        _FakeJwksClient.urls.clear()

    def test_default_oauth_urls_are_canonical_https(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_oauth_issuer(), "https://shizuha.com/id")
            self.assertEqual(
                _resource_server_url("mail"),
                "https://shizuha.com/mcp/mail",
            )

    def test_explicit_oauth_urls_override_canonical_defaults(self):
        with mock.patch.dict(
            os.environ,
            {
                "SHIZUHA_OAUTH_ISSUER": "https://id.example/",
                "SHIZUHA_MCP_RESOURCE_URL": "https://mcp.example/mail/",
            },
            clear=True,
        ):
            self.assertEqual(_oauth_issuer(), "https://id.example")
            self.assertEqual(
                _resource_server_url("mail"),
                "https://mcp.example/mail",
            )

    def test_hs256_shared_secret_token_is_rejected(self):
        token = jwt.encode(
            {"user_id": 7, "exp": int(time.time()) + 3600},
            "secret",
            algorithm="HS256",
        )
        verifier = ShizuhaTokenVerifier("test", "https://id.example")

        result = asyncio.run(verifier.verify_token(token))

        self.assertIsNone(result)

    def test_rs256_jwks_token_authenticates(self):
        token = "header.payload.signature"
        with mock.patch.dict(os.environ, {}, clear=True):
            verifier = ShizuhaTokenVerifier("test", _oauth_issuer())
            with mock.patch("jwt.PyJWKClient", _FakeJwksClient), mock.patch(
                "base.jwt.decode",
                return_value={"user_id": 7, "exp": int(time.time()) + 3600},
            ) as decode:
                result = asyncio.run(verifier.verify_token(token))

        self.assertIsNotNone(result)
        self.assertEqual(result.client_id, "7")
        decode.assert_called_once()
        self.assertEqual(decode.call_args.kwargs["algorithms"], ["RS256"])
        self.assertEqual(
            _FakeJwksClient.urls,
            ["https://shizuha.com/id/.well-known/jwks.json"],
        )

    def test_discovery_advertises_canonical_https_issuer(self):
        class _FakeMcp:
            def __init__(self):
                self.routes = {}

            def custom_route(self, path, methods):
                def register(handler):
                    self.routes[path] = handler
                    return handler

                return register

        from starlette.requests import Request

        mcp = _FakeMcp()
        with mock.patch.dict(os.environ, {}, clear=True):
            _register_oauth_discovery_routes(mcp, "mail")
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "scheme": "https",
                    "path": "/.well-known/oauth-protected-resource",
                    "raw_path": b"/.well-known/oauth-protected-resource",
                    "query_string": b"",
                    "headers": [(b"host", b"shizuha.com")],
                    "server": ("shizuha.com", 443),
                    "client": ("127.0.0.1", 1),
                }
            )
            response = asyncio.run(
                mcp.routes["/.well-known/oauth-protected-resource"](request)
            )

        self.assertIn(
            b'"authorization_servers":["https://shizuha.com/id"]',
            response.body,
        )

    def test_expired_rs256_token_is_rejected_for_refresh(self):
        token = "header.payload.signature"
        verifier = ShizuhaTokenVerifier("test", "https://id.example")
        with mock.patch("jwt.PyJWKClient", _FakeJwksClient), mock.patch(
            "base.jwt.decode",
            side_effect=jwt.ExpiredSignatureError("expired"),
        ):
            result = asyncio.run(verifier.verify_token(token))

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
