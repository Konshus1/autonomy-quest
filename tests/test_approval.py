from __future__ import annotations

import unittest
from datetime import datetime, timezone

from runner.approval import ApprovalInvalid, assert_valid_approval


class ApprovalInvariantTests(unittest.TestCase):
    def test_valid_approval_requires_pending_with_timestamp(self):
        approval = assert_valid_approval({
            "id": 12,
            "status": "pending",
            "approved_at": datetime.now(timezone.utc),
            "summary": "ship approved work",
        })

        self.assertEqual(approval.work_id, 12)

    def test_unapproved_pending_work_is_not_an_approval(self):
        with self.assertRaisesRegex(ApprovalInvalid, "approved_at"):
            assert_valid_approval({"id": 12, "status": "pending", "approved_at": None})

    def test_non_pending_status_is_not_executable_approval(self):
        for status in ("awaiting_human", "running", "done", "abandoned"):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ApprovalInvalid, "not pending"):
                    assert_valid_approval({
                        "id": 12,
                        "status": status,
                        "approved_at": datetime.now(timezone.utc),
                    })

    def test_missing_row_fails_closed(self):
        with self.assertRaisesRegex(ApprovalInvalid, "missing"):
            assert_valid_approval(None)


if __name__ == "__main__":
    unittest.main()
