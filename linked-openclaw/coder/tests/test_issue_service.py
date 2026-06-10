import unittest

from src.clients.feishu_client import build_feishu_thread_session_key
from src.issue_service import (
    IssueService,
    build_repo_alias_map,
    is_feishu_help_command,
    parse_feishu_model_command,
    parse_feishu_issue_command,
)


class IssueServiceSessionKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "execution_backend": "codex",
            "codex_model": "gpt-5.4",
            "openclaw_session_prefix": "gh",
            "issue_branch_prefix": "coder",
            "allowed_repos": ["yeying-community/deployer", "yeying-community/robot"],
            "repo_aliases": {"chat": "yeying-community/chat"},
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

        self.assertIn("Issue 正文：", prompt)
        self.assertIn("只需要新增一个测试文件。", prompt)
        self.assertNotIn("gh issue view", prompt)

    def test_build_feishu_handoff_thread_prompt_is_user_facing(self) -> None:
        prompt = self.service.build_feishu_handoff_thread_prompt(
            "yeying-community/robot",
            36,
        )

        self.assertIn("已切换到 yeying-community/robot#36 的讨论线程。", prompt)
        self.assertIn("再在线程里发送 `/run`。", prompt)
        self.assertNotIn("会话标识：", prompt)
        self.assertNotIn("Issue 正文：", prompt)

    def test_parse_feishu_issue_command_supports_list_and_select(self) -> None:
        self.assertEqual(parse_feishu_issue_command("/issue_deployer"), ("deployer", None))
        self.assertEqual(parse_feishu_issue_command("/issue_deployer 95"), ("deployer", 95))
        self.assertEqual(parse_feishu_issue_command("/issue_deployer #95"), ("deployer", 95))
        self.assertEqual(parse_feishu_issue_command("/issue_deployer#95"), ("deployer", 95))
        self.assertEqual(parse_feishu_issue_command("@_user_1 /issue_robot"), ("robot", None))
        self.assertEqual(parse_feishu_issue_command("@_user_1/issue_chat"), ("chat", None))
        self.assertEqual(parse_feishu_issue_command("@_user_1 /issue_robot #12"), ("robot", 12))
        self.assertEqual(parse_feishu_issue_command("@_user_1 /issue_robot#12"), ("robot", 12))
        self.assertIsNone(parse_feishu_issue_command("hello"))

    def test_is_feishu_help_command_supports_leading_mentions(self) -> None:
        self.assertTrue(is_feishu_help_command("/help"))
        self.assertTrue(is_feishu_help_command("@_user_1 /help"))
        self.assertFalse(is_feishu_help_command("/issue_robot"))

    def test_parse_feishu_model_command_supports_set_and_reset(self) -> None:
        self.assertEqual(parse_feishu_model_command("/model gpt-5.4"), "gpt-5.4")
        self.assertEqual(parse_feishu_model_command("@_user_1 /model gpt-5.4-mini"), "gpt-5.4-mini")
        self.assertEqual(parse_feishu_model_command("/model default"), "")
        self.assertEqual(parse_feishu_model_command("/model reset"), "")
        self.assertIsNone(parse_feishu_model_command("/model"))

    def test_build_repo_alias_map_uses_repo_name_and_explicit_mapping(self) -> None:
        aliases = build_repo_alias_map(self.config)

        self.assertEqual(aliases["deployer"], "yeying-community/deployer")
        self.assertEqual(aliases["robot"], "yeying-community/robot")
        self.assertEqual(aliases["chat"], "yeying-community/chat")

    def test_handle_feishu_chat_command_help_replies_with_usage(self) -> None:
        replies: list[tuple[str, str]] = []
        self.service.feishu_send_text_message = lambda chat_id, text: replies.append((chat_id, text)) or "msg-1"

        handled = self.service.handle_feishu_chat_command(
            "oc_help_chat",
            {"content": "@_user_1 /help"},
        )

        self.assertTrue(handled)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0][0], "oc_help_chat")
        self.assertIn("/issue_<repo>", replies[0][1])
        self.assertIn("/issue_<repo> #<issue_number>", replies[0][1])
        self.assertIn("再在线程里发送 `/run`", replies[0][1])

    def test_handle_feishu_chat_command_list_failure_replies_with_error(self) -> None:
        replies: list[tuple[str, str]] = []
        self.service.feishu_send_text_message = lambda chat_id, text: replies.append((chat_id, text)) or "msg-1"
        self.service.list_pending_repo_issues = lambda repo_full_name, limit=10: (_ for _ in ()).throw(RuntimeError("boom"))

        handled = self.service.handle_feishu_chat_command(
            "oc_help_chat",
            {"content": "@_user_1 /issue_robot"},
        )

        self.assertTrue(handled)
        self.assertEqual(len(replies), 1)
        self.assertIn("读取 yeying-community/robot 的 issue 列表失败", replies[0][1])
        self.assertIn("boom", replies[0][1])

    def test_reply_issue_discussion_to_feishu_model_command_updates_session(self) -> None:
        replies: list[str] = []
        updates: list[dict[str, object]] = []
        self.service.fetchone = lambda query, params=(): {"issue_title": "Issue 36"}
        self.service.ensure_issue_session = lambda repo_full_name, issue_number, issue_title: {
            "session_key": "gh-yeying-community-robot-issue-36",
            "agent_session_id": "thread_1",
            "handoff_prompt": "prompt",
            "selected_model": None,
        }
        self.service.upsert_issue_session = lambda repo_full_name, issue_number, **kwargs: (
            updates.append(kwargs)
            or {
                "session_key": "gh-yeying-community-robot-issue-36",
                "agent_session_id": "thread_1",
                "handoff_prompt": "prompt",
                "selected_model": kwargs.get("selected_model"),
            }
        )
        self.service.feishu_reply_in_thread = lambda root_message_id, text: replies.append(text) or "msg-1"

        agent_session_id = self.service.reply_issue_discussion_to_feishu(
            "yeying-community/robot",
            36,
            binding={"root_message_id": "om_root", "prompt_message_id": "", "thread_id": "omt_1"},
            recent_messages=[{"message_id": "om_user_1", "content": "@_user_1 /model gpt-5.4-mini"}],
        )

        self.assertEqual(agent_session_id, "thread_1")
        self.assertEqual(updates[0]["selected_model"], "gpt-5.4-mini")
        self.assertFalse(updates[0].get("clear_selected_model"))
        self.assertIn("当前 issue 模型已设置为 `gpt-5.4-mini`", replies[0])

    def test_reply_issue_discussion_to_feishu_model_default_clears_session(self) -> None:
        replies: list[str] = []
        updates: list[dict[str, object]] = []
        self.service.fetchone = lambda query, params=(): {"issue_title": "Issue 36"}
        self.service.ensure_issue_session = lambda repo_full_name, issue_number, issue_title: {
            "session_key": "gh-yeying-community-robot-issue-36",
            "agent_session_id": "thread_1",
            "handoff_prompt": "prompt",
            "selected_model": "gpt-5.5",
        }
        self.service.upsert_issue_session = lambda repo_full_name, issue_number, **kwargs: (
            updates.append(kwargs)
            or {
                "session_key": "gh-yeying-community-robot-issue-36",
                "agent_session_id": "thread_1",
                "handoff_prompt": "prompt",
                "selected_model": None,
            }
        )
        self.service.feishu_reply_in_thread = lambda root_message_id, text: replies.append(text) or "msg-1"

        self.service.reply_issue_discussion_to_feishu(
            "yeying-community/robot",
            36,
            binding={"root_message_id": "om_root", "prompt_message_id": "", "thread_id": "omt_1"},
            recent_messages=[{"message_id": "om_user_1", "content": "@_user_1 /model default"}],
        )

        self.assertIsNone(updates[0]["selected_model"])
        self.assertTrue(updates[0]["clear_selected_model"])
        self.assertIn("已恢复使用默认模型 `gpt-5.4`", replies[0])


if __name__ == "__main__":
    unittest.main()
