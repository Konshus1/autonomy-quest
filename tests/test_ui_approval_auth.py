from __future__ import annotations

import http.client
import json
import os
import threading
import unittest
from datetime import datetime, timezone
from http.server import HTTPServer
from unittest.mock import patch

from ui.server import Handler


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, args=()):
        self.sql = sql
        self.args = args

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self, *args, **kwargs):
        return FakeCursor(self.row)


class ApprovalEndpointAuthTests(unittest.TestCase):
    def setUp(self):
        self.old_token = os.environ.pop("AQ_APPROVAL_TOKEN", None)

    def tearDown(self):
        if self.old_token is not None:
            os.environ["AQ_APPROVAL_TOKEN"] = self.old_token
        else:
            os.environ.pop("AQ_APPROVAL_TOKEN", None)

    def post(self, *, token=None, bearer=None, row=None):
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        headers = {}
        if token is not None:
            headers["X-AQ-Approval-Token"] = token
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"

        with patch("ui.server._conn", return_value=FakeConn(row)):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request("POST", "/api/approve/42", headers=headers)
            resp = conn.getresponse()
            body = json.loads(resp.read().decode())
            conn.close()

        thread.join(timeout=5)
        server.server_close()
        return resp.status, body

    def approved_row(self):
        return {
            "id": 42,
            "kind": "test",
            "summary": "approve me",
            "rationale": "because",
            "requires_human": True,
            "status": "pending",
            "approved_at": datetime.now(timezone.utc),
        }

    def test_approve_fails_closed_when_token_unset(self):
        status, body = self.post(row=self.approved_row())

        self.assertEqual(status, 403)
        self.assertFalse(body["approved"])
        self.assertIn("not configured", body["error"])

    def test_approve_rejects_missing_or_wrong_token(self):
        os.environ["AQ_APPROVAL_TOKEN"] = "secret"

        missing_status, _ = self.post(row=self.approved_row())
        wrong_status, _ = self.post(token="wrong", row=self.approved_row())

        self.assertEqual(missing_status, 403)
        self.assertEqual(wrong_status, 403)

    def test_approve_accepts_header_or_bearer_token(self):
        os.environ["AQ_APPROVAL_TOKEN"] = "secret"

        header_status, header_body = self.post(token="secret", row=self.approved_row())
        bearer_status, bearer_body = self.post(bearer="secret", row=self.approved_row())

        self.assertEqual(header_status, 200)
        self.assertTrue(header_body["approved"])
        self.assertEqual(bearer_status, 200)
        self.assertTrue(bearer_body["approved"])

    def test_approve_conflict_is_explicit(self):
        os.environ["AQ_APPROVAL_TOKEN"] = "secret"

        status, body = self.post(token="secret", row=None)

        self.assertEqual(status, 409)
        self.assertFalse(body["approved"])


if __name__ == "__main__":
    unittest.main()
