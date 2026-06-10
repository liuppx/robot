import unittest
from unittest.mock import patch

from src.clients.feishu_client import normalize_confirm_text, parse_feishu_message_text, resolve_feishu_runtime_settings


class FeishuSettingsTests(unittest.TestCase):
    @patch("src.clients.feishu_client.load_openclaw_config_json")
    def test_resolve_feishu_runtime_settings_merges_multiple_chat_ids(self, mock_load) -> None:
        mock_load.return_value = {
            "channels": {
                "feishu": {
                    "appId": "app-id",
                    "appSecret": "secret",
                    "groupAllowFrom": ["oc_group_a", "oc_group_b"],
                }
            }
        }
        config = {
            "feishu_handoff_chat_id": "oc_group_b",
            "feishu_handoff_chat_ids": ["oc_group_c", "oc_group_a"],
            "feishu_account_id": "default",
        }

        settings = resolve_feishu_runtime_settings(config)

        self.assertEqual(settings["chat_id"], "oc_group_b")
        self.assertEqual(settings["chat_ids"], ["oc_group_b", "oc_group_c", "oc_group_a"])

    def test_normalize_confirm_text_strips_leading_feishu_mentions(self) -> None:
        self.assertEqual(normalize_confirm_text("@_user_1 /run"), "/run")
        self.assertEqual(normalize_confirm_text(" @_user_1   @_user_2   /issue_robot "), "/issue_robot")
        self.assertEqual(normalize_confirm_text("@_user_1/issue_chat"), "/issue_chat")

    def test_parse_feishu_post_message_text_flattens_rich_text(self) -> None:
        item = {
            "msg_type": "post",
            "body": {
                "content": (
                    '{"title":"","content":['
                    '  [{"tag":"text","text":"第一行"}],'
                    '  [{"tag":"text","text":"第二行"},{"tag":"text","text":"补充"}]'
                    " ]}"
                )
            },
        }

        self.assertEqual(parse_feishu_message_text(item), "第一行\n第二行补充")


if __name__ == "__main__":
    unittest.main()
