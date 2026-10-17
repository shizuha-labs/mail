"""
MAIL-91 regression tests: the MCP -> REST request contract for folder selection.

``GET /mail/api/messages/`` resolves the canonical system folders only when they
arrive as ``folder_role``; ``folder`` is a server-side mailbox id (or a local
MailFolder UUID). ``mail_list_messages`` used to send ``folder=inbox``, which
matched nothing, while ``folder=Inbox`` was forwarded to JMAP as a raw mailbox
id and failed upstream. These tests pin the emitted query parameters so the
mapping cannot silently regress.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# mail_server resolves its API URL/token at import time. Pin them to unroutable
# test values so importing the module never depends on — or reaches — a real
# deployment; every request in these tests is captured by a stub.
os.environ["SHIZUHA_MAIL_URL"] = "http://mail.invalid/mail/api"
os.environ["SHIZUHA_MAIL_JWT_TOKEN"] = "test-token"

import mail_server  # noqa: E402


class _RecordingAPI:
    """Stand-in for base.api_request that records the outbound request."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, path, params=None, **kwargs):
        self.calls.append({"method": method, "path": path, "params": params})
        return {"results": []}

    @property
    def last(self):
        return self.calls[-1]


class FolderQueryParamsTests(unittest.TestCase):
    """Unit coverage for the name -> selector mapping itself."""

    def test_every_system_role_maps_to_folder_role(self):
        for role in ("inbox", "drafts", "sent", "trash", "junk", "archive"):
            with self.subTest(role=role):
                self.assertEqual(
                    mail_server.folder_query_params(role), {"folder_role": role}
                )

    def test_system_roles_are_case_insensitive(self):
        for name, role in (
            ("Inbox", "inbox"),
            ("SENT", "sent"),
            ("Drafts", "drafts"),
            ("TrAsH", "trash"),
            ("Junk", "junk"),
            ("ARCHIVE", "archive"),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    mail_server.folder_query_params(name), {"folder_role": role}
                )

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(
            mail_server.folder_query_params("  Inbox  "), {"folder_role": "inbox"}
        )

    def test_custom_names_pass_through_as_folder(self):
        for name in ("Project Alpha", "Clients/Acme", "inbox-archive"):
            with self.subTest(name=name):
                self.assertEqual(
                    mail_server.folder_query_params(name), {"folder": name}
                )

    def test_missing_name_defaults_to_the_inbox_role(self):
        for name in (None, "", "   "):
            with self.subTest(name=name):
                self.assertEqual(
                    mail_server.folder_query_params(name), {"folder_role": "inbox"}
                )


class ListMessagesRequestContractTests(unittest.TestCase):
    """The parameters mail_list_messages actually puts on the wire."""

    def setUp(self):
        self.api = _RecordingAPI()
        self._original = mail_server.base.api_request
        mail_server.base.api_request = self.api
        self.addCleanup(setattr, mail_server.base, "api_request", self._original)

    def test_default_call_requests_the_inbox_role(self):
        mail_server.list_messages()

        self.assertEqual(self.api.last["method"], "GET")
        self.assertEqual(self.api.last["path"], "/messages/")
        self.assertEqual(self.api.last["params"].get("folder_role"), "inbox")
        self.assertNotIn("folder", self.api.last["params"])

    def test_mixed_case_system_folder_is_normalised_to_a_role(self):
        mail_server.list_messages(folder_name="Inbox")

        self.assertEqual(self.api.last["params"].get("folder_role"), "inbox")
        self.assertNotIn("folder", self.api.last["params"])

    def test_custom_folder_name_is_sent_as_folder(self):
        mail_server.list_messages(folder_name="Project Alpha")

        self.assertEqual(self.api.last["params"].get("folder"), "Project Alpha")
        self.assertNotIn("folder_role", self.api.last["params"])

    def test_limit_and_flag_filters_survive_the_mapping(self):
        mail_server.list_messages(
            folder_name="SENT", unread_only=True, flagged_only=True, limit=7
        )

        params = self.api.last["params"]
        self.assertEqual(params.get("folder_role"), "sent")
        self.assertEqual(params.get("page_size"), 7)
        self.assertIs(params.get("is_read"), False)
        self.assertIs(params.get("is_flagged"), True)

    def test_explicit_folder_id_still_uses_the_folder_messages_endpoint(self):
        mail_server.list_messages(
            folder_id="1b0f4a5e-0000-4000-8000-000000000000", folder_name="Inbox"
        )

        self.assertEqual(
            self.api.last["path"],
            "/folders/1b0f4a5e-0000-4000-8000-000000000000/messages/",
        )
        self.assertEqual(self.api.last["params"], {"page_size": 20})


class ListThreadsRequestContractTests(unittest.TestCase):
    """MAIL-92: the parameters mail_list_threads actually puts on the wire.

    ``mail_list_threads`` used to send ``folder_id``/``folder`` that the
    server-side ``MailThreadViewSet`` never read, so every folder selector was
    silently discarded and the endpoint returned all threads on the account.
    These tests pin the emitted query parameters to the same ``folder`` /
    ``folder_role`` contract ``MailMessageViewSet`` already honours.
    """

    def setUp(self):
        self.api = _RecordingAPI()
        self._original = mail_server.base.api_request
        mail_server.base.api_request = self.api
        self.addCleanup(setattr, mail_server.base, "api_request", self._original)

    def test_default_call_requests_the_inbox_role(self):
        mail_server.list_threads()

        self.assertEqual(self.api.last["method"], "GET")
        self.assertEqual(self.api.last["path"], "/threads/")
        self.assertEqual(self.api.last["params"].get("folder_role"), "inbox")
        self.assertNotIn("folder", self.api.last["params"])

    def test_system_folder_name_maps_to_folder_role(self):
        for name, role in (("sent", "sent"), ("Inbox", "inbox"), ("TRASH", "trash")):
            with self.subTest(name=name):
                mail_server.list_threads(folder_name=name)
                params = self.api.last["params"]
                self.assertEqual(params.get("folder_role"), role)
                self.assertNotIn("folder", params)

    def test_custom_folder_name_is_sent_as_folder(self):
        mail_server.list_threads(folder_name="Project Alpha")

        params = self.api.last["params"]
        self.assertEqual(params.get("folder"), "Project Alpha")
        self.assertNotIn("folder_role", params)

    def test_explicit_folder_id_is_sent_as_folder(self):
        mail_server.list_threads(
            folder_id="1b0f4a5e-0000-4000-8000-000000000000", folder_name="Inbox"
        )

        params = self.api.last["params"]
        self.assertEqual(
            params.get("folder"), "1b0f4a5e-0000-4000-8000-000000000000"
        )
        self.assertNotIn("folder_role", params)
        self.assertEqual(params.get("page_size"), 20)

    def test_limit_survives_the_mapping(self):
        mail_server.list_threads(folder_name="SENT", limit=7)

        params = self.api.last["params"]
        self.assertEqual(params.get("folder_role"), "sent")
        self.assertEqual(params.get("page_size"), 7)


if __name__ == "__main__":
    unittest.main()
