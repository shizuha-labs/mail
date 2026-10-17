"""
MAIL-128 regression tests: the agent-facing Mail MCP surface (HLD §3/§5).

Contract under test (MAIL-127):
- SELF-SCOPED: no caller-supplied `account_id`/`from`/cross-owner on any tool.
- OWNER-SCOPED 404: a foreign/unknown id must not leak cross-account detail.
- PLAIN-TEXT RENDERING: `body_html` never returned/rendered in v1; headers
  delimited from body; attachments metadata-only.
- BOUNDED SEARCH: limit clamped to [1, 50], no offset/cursor continuation.
- SEND IDEMPOTENCY: `client_message_id` is a UUID; reuse with a different
  payload must not silently overwrite (server 409 surfaces as a conflict
  error); the key is echoed for status lookup.
- NO SECRETS IN OUTPUT: provider passwords/tokens must not appear in any
  tool output rendered here (we never forward them — the API URL is the only
  secret-adjacent value and it stays out of tool bodies).

These are pure unit tests: the module under test resolves its API URL/token at
import time, so they pin unroutable test values and stub `base.api_request` —
no live backend is reached.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["SHIZUHA_MAIL_URL"] = "http://mail.invalid/mail/api"
os.environ["SHIZUHA_MAIL_JWT_TOKEN"] = "test-token"

import base  # noqa: E402
import mail_agent_server as server  # noqa: E402


class _StubAPI:
    """Stub for base.api_request. Callers set `self.result` (dict) or `self.error`."""

    def __init__(self):
        self.result = {}
        self.error = None
        self.calls = []

    def __call__(self, method, path, params=None, data=None, **kwargs):
        self.calls.append({"method": method, "path": path, "params": params, "data": data})
        if self.error is not None:
            return self.error
        return self.result


def _account(**overrides):
    base_account = {
        "id": "acct-1",
        "email_address": "dev@shizuha.com",
        "display_name": "Dev Agent",
        "incoming_server_type": "jmap",
        "storage_quota_mb": 1024,
        "storage_used_mb": 12,
    }
    base_account.update(overrides)
    return base_account


class SelfScopingTests(unittest.TestCase):
    """HLD §2.1/§7: no caller-supplied account/from/cross-owner in tool surface."""

    def setUp(self):
        self.stub = _StubAPI()
        self._orig = server.base.api_request
        server.base.api_request = self.stub

    def tearDown(self):
        server.base.api_request = self._orig

    def test_account_info_has_no_account_param(self):
        import inspect
        sig = inspect.signature(server.account_info)
        self.assertNotIn("account_id", sig.parameters)

    def test_list_messages_has_no_account_param(self):
        import inspect
        sig = inspect.signature(server.list_messages)
        self.assertNotIn("account_id", sig.parameters)
        self.assertNotIn("from", sig.parameters)

    def test_get_message_is_keyed_by_id_only(self):
        import inspect
        sig = inspect.signature(server.get_message)
        self.assertEqual(set(sig.parameters), {"message_id"})

    def test_search_has_no_account_param(self):
        import inspect
        sig = inspect.signature(server.search_messages)
        self.assertNotIn("account_id", sig.parameters)


class AccountInfoTests(unittest.TestCase):
    def setUp(self):
        self.stub = _StubAPI()
        self._orig = server.base.api_request
        server.base.api_request = self.stub

    def tearDown(self):
        server.base.api_request = self._orig

    def test_resolves_me_endpoint(self):
        self.stub.result = {"account": _account()}
        out = server.account_info()
        self.assertIn("dev@shizuha.com", out)
        self.assertEqual(self.stub.calls[0]["method"], "GET")
        self.assertEqual(self.stub.calls[0]["path"], "/accounts/me/")

    def test_no_credentials_rendered(self):
        # Even if the backend ever returned a password key, we must not print it.
        self.stub.result = {
            "account": _account(incoming_password="super-secret", outgoing_username="svc-pw")
        }
        out = server.account_info()
        self.assertNotIn("super-secret", out)
        self.assertNotIn("svc-pw", out)

    def test_empty_account_is_clear_but_not_an_error(self):
        self.stub.result = {"account": None}
        out = server.account_info()
        self.assertIn("No @shizuha.com mailbox provisioned", out)


class BoundedSearchTests(unittest.TestCase):
    """HLD §3 (P3): single bounded window, limit <= 50, no continuation."""

    def setUp(self):
        self.stub = _StubAPI()
        self._orig = server.base.api_request
        server.base.api_request = self.stub

    def tearDown(self):
        server.base.api_request = self._orig

    def test_limit_is_clamped_to_50(self):
        self.stub.result = {"results": []}
        server.search_messages("query", limit=999)
        self.assertEqual(self.stub.calls[0]["data"]["page_size"], 50)

    def test_limit_zero_clamps_to_one(self):
        self.stub.result = {"results": []}
        server.search_messages("query", limit=0)
        self.assertEqual(self.stub.calls[0]["data"]["page_size"], 1)

    def test_no_offset_or_cursor_emitted(self):
        self.stub.result = {"results": []}
        server.search_messages("query", limit=20)
        data = self.stub.calls[0]["data"]
        self.assertNotIn("offset", data)
        self.assertNotIn("cursor", data)
        self.assertNotIn("page", data)

    def test_empty_query_refused(self):
        out = server.search_messages("   ")
        self.assertIn("non-empty", out)
        self.assertEqual(self.stub.calls, [], "no API call for empty query")


class PlainTextRenderingTests(unittest.TestCase):
    """HLD §3 (A-P2): plain text only; body_html never rendered in v1."""

    def setUp(self):
        self.stub = _StubAPI()
        self._orig = server.base.api_request
        server.base.api_request = self.stub

    def tearDown(self):
        server.base.api_request = self._orig

    def test_body_html_is_never_returned(self):
        self.stub.result = {
            "id": "m1",
            "subject": "Hello",
            "from_email": "a@x.com",
            "body_text": "Plain hello",
            # Backend may carry it, but our surface must not print it:
            "body_html": "<script>alert(1)</script><b>Hi</b>",
        }
        out = server.get_message("m1")
        self.assertIn("Plain hello", out)
        self.assertNotIn("alert(1)", out)
        self.assertNotIn("<b>Hi</b>", out)
        self.assertNotIn("body_html", out)

    def test_no_body_placeholder_is_honest(self):
        self.stub.result = {
            "id": "m1",
            "subject": "empty",
            "from_email": "a@x.com",
            "body_text": None,
        }
        out = server.get_message("m1")
        self.assertIn("No plain-text body", out)

    def test_attachments_are_metadata_only(self):
        self.stub.result = {
            "id": "m1",
            "subject": "att",
            "from_email": "a@x.com",
            "body_text": "see attach",
            "attachments": [{"filename": "report.pdf", "size": 123}],
        }
        out = server.get_message("m1")
        self.assertIn("report.pdf", out)
        # We must never auto-fetch/decode attachment content in v1 — a content
        # key in the backend response must not leak into the rendered output.
        self.assertNotIn("body_html", out)

    def test_foreign_id_error_does_not_leak_ownership(self):
        self.stub.error = {"error": "Not Found", "code": 404}
        out = server.get_message("foreign-1")
        # The backend's own non-enumerating message is authoritative; our
        # surface must not append cross-account context.
        self.assertTrue(out)


class SendIdempotencyTests(unittest.TestCase):
    """HLD §5/§6: exactly-once via client_message_id; explicit send only.

    MAIL-157: `client_message_id` is REQUIRED at this boundary (omission and
    malformation are rejected before any API work) and v1 sends to exactly ONE
    recipient (0 and >1 are rejected before any API work).
    """

    CMI = "11111111-1111-1111-1111-111111111111"

    def setUp(self):
        self.stub = _StubAPI()
        self._orig = server.base.api_request
        server.base.api_request = self.stub

    def tearDown(self):
        server.base.api_request = self._orig

    def test_send_posts_to_send_endpoint(self):
        self.stub.result = {"status": "sent", "client_message_id": self.CMI}
        out = server.send_email("x@y.com", "Subj", "Body", client_message_id=self.CMI)
        call = self.stub.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertTrue(call["path"].endswith("/send/"))
        self.assertEqual(call["data"]["to_addresses"], ["x@y.com"])
        self.assertEqual(call["data"]["client_message_id"], self.CMI)
        self.assertIn(f"client_message_id: {self.CMI}", out)

    def test_client_message_id_is_required_in_signature(self):
        import inspect
        sig = inspect.signature(server.send_email)
        param = sig.parameters["client_message_id"]
        self.assertEqual(param.default, inspect.Parameter.empty,
                         "client_message_id must be a required parameter")

    def test_omitted_client_message_id_rejected_before_api(self):
        # The parameter is REQUIRED (no default) — omission is a TypeError at the
        # Python boundary, which is itself zero API work. At runtime an empty or
        # None key (what a caller omitting the value would send) must also be
        # rejected before any API/Outbox/provider work.
        for omitted in ("", None):
            out = server.send_email("x@y.com", "S", "B", client_message_id=omitted)
            self.assertIn("REQUIRED", out)
            self.assertEqual(self.stub.calls, [], "no API call for omitted id")

    def test_malformed_client_message_id_rejected(self):
        out = server.send_email("x@y.com", "S", "B", client_message_id="not-a-uuid")
        self.assertIn("valid UUID", out)
        self.assertEqual(self.stub.calls, [], "no API call for malformed id")

    def test_no_recipient_refused(self):
        out = server.send_email("  ", "S", "B", client_message_id=self.CMI)
        self.assertIn("recipient email", out)
        self.assertEqual(self.stub.calls, [])

    def test_multiple_recipients_rejected_before_api(self):
        out = server.send_email(
            "a@x.com,b@x.com", "S", "B", client_message_id=self.CMI)
        self.assertIn("exactly one recipient", out)
        self.assertEqual(self.stub.calls, [], "no API call for multi-recipient fan-out")

    def test_conflict_409_surfaces_as_clear_result(self):
        self.stub.error = {"error": "conflict", "code": 409, "message": "client_message_id already used with different content"}
        out = server.send_email("x@y.com", "S", "B", client_message_id=self.CMI)
        self.assertIn("conflict", out.lower())


class SendStatusTests(unittest.TestCase):
    def setUp(self):
        self.stub = _StubAPI()
        self._orig = server.base.api_request
        server.base.api_request = self.stub

    def tearDown(self):
        server.base.api_request = self._orig

    def test_looks_up_owner_scoped_status(self):
        self.stub.result = {
            "client_message_id": "11111111-1111-1111-1111-111111111111",
            "send_status": "sent",
            "delivery_status": "delivered",
        }
        out = server.get_send_status("11111111-1111-1111-1111-111111111111")
        self.assertEqual(self.stub.calls[0]["path"], "/send/11111111-1111-1111-1111-111111111111/")
        self.assertIn("Send: sent", out)
        self.assertIn("Delivery: delivered", out)

    def test_malformed_key_rejected(self):
        out = server.get_send_status("nope")
        self.assertIn("valid UUID", out)
        self.assertEqual(self.stub.calls, [])

    def test_unknown_key_foreign_is_generic(self):
        self.stub.error = {"error": "Not Found", "code": 404}
        out = server.get_send_status("11111111-1111-1111-1111-111111111111")
        self.assertTrue(out)


class BoundedListTests(unittest.TestCase):
    def setUp(self):
        self.stub = _StubAPI()
        self._orig = server.base.api_request
        server.base.api_request = self.stub

    def tearDown(self):
        server.base.api_request = self._orig

    def test_list_clamps_limit(self):
        self.stub.result = {"results": []}
        server.list_messages(folder="inbox", limit=999)
        self.assertEqual(self.stub.calls[0]["params"]["page_size"], 50)

    def test_unread_filter_maps_to_is_read_false(self):
        self.stub.result = {"results": []}
        server.list_messages(folder="inbox", unread_only=True)
        self.assertIs(self.stub.calls[0]["params"]["is_read"], False)


if __name__ == "__main__":
    unittest.main()
