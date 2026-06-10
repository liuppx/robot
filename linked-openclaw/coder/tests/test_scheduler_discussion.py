import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.db as db_module
from src.scheduler import SchedulerContext, scan_waiting_feishu_confirmations


class SchedulerDiscussionTests(unittest.TestCase):
    def test_scan_waiting_feishu_confirmations_batches_multiple_user_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "db_path": str(Path(tmpdir) / "issue_bot.db"),
                "feishu_thread_scan_limit": 30,
                "feishu_confirm_keywords": ["/run"],
            }
            db_module.init_db(config)
            now = "2026-05-28T00:00:00Z"
            db_module.upsert_issue_session(
                config,
                "yeying-community/robot",
                36,
                backend="codex",
                selected_model=None,
                session_key="gh-yeying-community-robot-issue-36",
                session_state="waiting_confirm",
                last_trigger_reason="test",
                last_triggered_at=now,
                handoff_prompt="internal prompt",
                agent_session_id="thread_1",
                branch_name="coder/issue-36-test",
                pr_url=None,
                summary=None,
                last_result_status="waiting_confirm",
                created_at=now,
                updated_at=now,
            )
            db_module.upsert_feishu_binding(
                config,
                chat_id="oc_test",
                thread_id="omt_test",
                repo_full_name="yeying-community/robot",
                issue_number=36,
                session_key="agent:gh-yeying-community-robot-issue-36:feishu:thread:oc_test:topic:omt_test",
                binding_state="waiting_confirm",
                note="auto handoff thread",
                root_message_id="om_root",
                prompt_message_id="om_prompt",
                last_seen_message_id="om_prompt",
                last_seen_message_time="100",
                confirm_message_id=None,
                confirm_message_time=None,
                created_at=now,
                updated_at=now,
            )

            calls: list[list[dict[str, object]]] = []

            context = SchedulerContext(
                config=config,
                runtime={},
                record_issue_trigger=lambda payload, reason: {},
                get_issue_session=lambda repo_full_name, issue_number: db_module.get_issue_session(
                    config, repo_full_name, issue_number
                ),
                issue_has_active_job=lambda repo_full_name, issue_number: False,
                upsert_feishu_binding=lambda _config, **kwargs: None,
                upsert_issue_session=lambda _config, repo_full_name, issue_number, **kwargs: None,
                handle_feishu_chat_command=lambda chat_id, message: False,
                reply_issue_discussion_to_feishu=lambda _config, repo_full_name, issue_number, **kwargs: calls.append(
                    list(kwargs["recent_messages"])
                ),
                confirm_feishu_binding_and_queue=lambda _config, binding, confirm_message: ("job-1", True),
            )

            messages = [
                {
                    "message_id": "om_prompt",
                    "thread_id": "omt_test",
                    "create_time": 100,
                    "sender_type": "app",
                    "content": "prompt",
                },
                {
                    "message_id": "om_user_1",
                    "thread_id": "omt_test",
                    "create_time": 101,
                    "sender_type": "user",
                    "content": "第一条",
                },
                {
                    "message_id": "om_user_2",
                    "thread_id": "omt_test",
                    "create_time": 102,
                    "sender_type": "user",
                    "content": "第二条",
                },
            ]

            with patch("src.scheduler.feishu_module.feishu_list_thread_messages", return_value=messages):
                scan_waiting_feishu_confirmations(context)

            self.assertEqual(len(calls), 1)
            self.assertEqual([item["message_id"] for item in calls[0]], ["om_prompt", "om_user_1", "om_user_2"])


if __name__ == "__main__":
    unittest.main()
