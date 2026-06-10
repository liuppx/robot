import unittest
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.worker import (
    build_missing_changes_retry_prompt,
    build_prompt,
    run_codex_chat_turn,
    summarize_openclaw_turn_failure,
)


class WorkerPromptTests(unittest.TestCase):
    def test_build_prompt_marks_run_as_already_confirmed(self) -> None:
        repo_path = "/tmp/repo"
        prompt = build_prompt(
            "yeying-community/deployer",
            {
                "number": 95,
                "title": "E2E联调测试",
                "body": "只新增一个测试文件。",
            },
            repo_path,
            "",
            session_key="gh-yeying-community-deployer-issue-95",
        )

        self.assertIn("飞书线程里已经收到明确的 `/run` 执行确认", prompt)
        self.assertIn("不要再等待新的确认消息", prompt)
        self.assertIn(f"当前真正的 Git 仓库根目录只有：{repo_path}", prompt)
        self.assertIn("不要把 `git status`、`gh issue view` 或其他 shell 命令当成继续任务的前置条件", prompt)
        self.assertIn("继续使用可直接读写文件的工具完成修改和验证", prompt)
        self.assertIn(f"它确实位于 `{repo_path}` 下", prompt)

    def test_missing_changes_retry_prompt_requires_real_repo_diff(self) -> None:
        repo_path = "/tmp/repo"
        prompt = build_missing_changes_retry_prompt(repo_path, "result: succeeded")

        self.assertIn("Git 仓库里仍然没有任何可提交改动", prompt)
        self.assertIn(f"真实 Git 仓库根目录：{repo_path}", prompt)
        self.assertIn("优先使用可直接读写文件的工具", prompt)
        self.assertIn("外层会再次检查 repo 是否真的有改动", prompt)

    def test_summarize_openclaw_turn_failure_surfaces_tool_error(self) -> None:
        summary = summarize_openclaw_turn_failure(
            "⚠️ Agent couldn't generate a response. Note: some tool actions may have already been executed — please verify before retrying.",
            "[tools] exec failed: exec denied: allowlist miss\n[agent/embedded] incomplete turn detected",
        )

        self.assertIsNotNone(summary)
        self.assertIn("openclaw turn failed before producing a structured final response", str(summary))
        self.assertIn("exec failed", str(summary))
        self.assertIn("incomplete turn detected", str(summary))

    def test_run_codex_chat_turn_uses_selected_model_override(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config = {
                "data_dir": tmpdir,
                "codex_bin": "codex",
                "codex_model": "gpt-5.4",
                "codex_timeout": 60,
                "codex_use_dangerously_bypass": True,
            }
            captured: list[list[str]] = []

            def fake_run_command(command, **kwargs):
                captured.append(list(command))
                return CompletedProcess(
                    command,
                    0,
                    stdout=(
                        '{"type":"thread.started","thread_id":"thread-123"}\n'
                        '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n'
                    ),
                    stderr="",
                )

            with patch("src.worker.build_codex_env", return_value={}), patch(
                "src.worker.run_command",
                side_effect=fake_run_command,
            ):
                turn = run_codex_chat_turn(
                    config,
                    Path(tmpdir),
                    "hello",
                    selected_model="gpt-5.5",
                )

        self.assertEqual(turn["text"], "ok")
        self.assertIn("-m", captured[0])
        self.assertIn("gpt-5.5", captured[0])


if __name__ == "__main__":
    unittest.main()
