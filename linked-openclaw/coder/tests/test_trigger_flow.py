import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.scheduler import (
    SchedulerContext,
    default_state,
    detect_poll_trigger,
    issue_comment_matches_trigger,
)


class TriggerFlowTests(unittest.TestCase):
    def test_issue_comment_matches_trigger_accepts_run(self) -> None:
        matched = issue_comment_matches_trigger(
            {
                "action": "created",
                "issue": {"number": 95},
                "comment": {"body": "/run"},
            },
            "/run",
        )

        self.assertTrue(matched)

    def test_issue_comment_matches_trigger_ignores_pull_request_comment(self) -> None:
        matched = issue_comment_matches_trigger(
            {
                "action": "created",
                "issue": {"number": 95, "pull_request": {"url": "https://api.github.com/repos/x/y/pulls/1"}},
                "comment": {"body": "/run"},
            },
            "/run",
        )

        self.assertFalse(matched)

    def test_detect_poll_trigger_accepts_issue_comment_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            context = SchedulerContext(
                config={
                    "trigger_comment": "/run",
                    "active_dir": str(Path(tmpdir) / "active"),
                },
                runtime={},
                record_issue_trigger=lambda payload, reason: {},
                get_issue_session=lambda repo_full_name, issue_number: None,
                issue_has_active_job=lambda repo_full_name, issue_number: False,
                upsert_feishu_binding=lambda **kwargs: None,
                upsert_issue_session=lambda repo_full_name, issue_number, **kwargs: None,
                reply_issue_discussion_to_feishu=lambda repo_full_name, issue_number, **kwargs: None,
                confirm_feishu_binding_and_queue=lambda binding, confirm_message: ("job-1", True),
            )
            issue = {"number": 95, "comments": 1}
            state = default_state()
            comment = {
                "id": 12345,
                "body": "/run",
                "created_at": "2026-05-10T00:00:00Z",
                "user": {"type": "User"},
            }
            with patch("src.scheduler.list_issue_comments", return_value=[comment]):
                decision, state_changed, issue_requests_rescan = detect_poll_trigger(
                    context,
                    "yeying-community/deployer",
                    issue,
                    state,
                    "token",
                    "yeying-community",
                    "deployer",
                )

        self.assertFalse(issue_requests_rescan)
        self.assertTrue(state_changed)
        self.assertIsNotNone(decision)
        payload, reason, key = decision or ({}, "", "")
        self.assertEqual(reason, "poll.issue_comment:/run")
        self.assertEqual(key, "yeying-community/deployer#95:comment:12345")
        self.assertEqual(payload["repository"]["full_name"], "yeying-community/deployer")
        self.assertEqual(payload["comment"]["body"], "/run")


if __name__ == "__main__":
    unittest.main()
