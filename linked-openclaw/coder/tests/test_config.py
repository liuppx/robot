import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.config as config_module


class ConfigTests(unittest.TestCase):
    def test_read_config_parses_multiple_feishu_handoff_chat_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "coder-bot.env"
            env_file.write_text(
                "\n".join(
                    [
                        "APP_HOME=..",
                        "FEISHU_HANDOFF_CHAT_ID=oc_group_a",
                        "FEISHU_HANDOFF_CHAT_IDS=oc_group_a,oc_group_b",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                config_module.load_env_file(env_file)
                config = config_module.read_config(env_file)

        self.assertEqual(config["feishu_handoff_chat_id"], "oc_group_a")
        self.assertEqual(config["feishu_handoff_chat_ids"], ["oc_group_a", "oc_group_b"])


if __name__ == "__main__":
    unittest.main()
