import unittest

from src.clients.feishu_client import build_feishu_thread_session_key
from src.issue_service import IssueService


class IssueServiceSessionKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "execution_backend": "openclaw",
            "openclaw_session_prefix": "gh",
            "issue_branch_prefix": "coder",
        }
        self.service = IssueService(self.config, {})

    def test_normalize_issue_session_key_replaces_feishu_route_key(self) -> None:
        route_key = build_feishu_thread_session_key(
            self.config,
            "yeying-community/deployer",
            88,
            "oc_chat",
            "omt_thread",
        )

        normalized = self.service.normalize_issue_session_key(
            "yeying-community/deployer",
            88,
            route_key,
        )

        self.assertEqual(normalized, "gh-yeying-community-deployer-issue-88")

    def test_normalize_issue_session_key_preserves_existing_local_key(self) -> None:
        normalized = self.service.normalize_issue_session_key(
            "yeying-community/deployer",
            88,
            "custom-stable-session",
        )

        self.assertEqual(normalized, "custom-stable-session")

    def test_build_handoff_prompt_does_not_require_gh_issue_view(self) -> None:
        prompt = self.service.build_handoff_prompt(
            "yeying-community/deployer",
            {
                "number": 95,
                "title": "E2E联调测试",
                "body": "只需要新增一个测试文件。",
            },
            "gh-yeying-community-deployer-issue-95",
        )

        self.assertIn("当前提示里已经附带 Issue 正文", prompt)
        self.assertIn("不要把 `gh issue view` 当成继续讨论的前置条件", prompt)


if __name__ == "__main__":
    unittest.main()
