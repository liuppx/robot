import unittest

from src.clients.openclaw_client import apply_openclaw_feishu_passive_mode


class OpenClawPassiveModeTests(unittest.TestCase):
    def test_apply_openclaw_feishu_passive_mode_disables_direct_chat_handling(self) -> None:
        config = {"openclaw_feishu_passive_mode": True}
        payload = {
            "channels": {
                "feishu": {
                    "enabled": True,
                    "dmPolicy": "allowlist",
                    "allowFrom": ["ou_user"],
                    "groupPolicy": "allowlist",
                    "groupAllowFrom": ["oc_group"],
                    "groups": {"oc_group": {"requireMention": True}},
                }
            }
        }

        result = apply_openclaw_feishu_passive_mode(config, payload)
        feishu = result["channels"]["feishu"]

        self.assertEqual(feishu["dmPolicy"], "allowlist")
        self.assertEqual(feishu["allowFrom"], [])
        self.assertEqual(feishu["groupPolicy"], "allowlist")
        self.assertEqual(feishu["groupAllowFrom"], [])
        self.assertEqual(feishu["groups"], {})
        self.assertEqual(payload["channels"]["feishu"]["groupAllowFrom"], ["oc_group"])


if __name__ == "__main__":
    unittest.main()
