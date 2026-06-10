from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.clients.github_client import (
    comment_issue,
    create_fork,
    create_pull_request,
    get_installation_token,
    get_repo_info,
    get_repo_info_optional,
    list_pull_requests,
)
from src.clients.codex_client import build_codex_env, parse_codex_jsonl_events, parse_codex_jsonl_usage
from src.clients.openclaw_client import (
    build_openclaw_env,
    list_openclaw_agents,
    openclaw_issue_workspace_dir,
)
from src.utils.helpers import (
    ensure_dir,
    now_utc,
    run_command,
    service_actor_name,
    slugify,
    tail_text,
)


OPENCLAW_RUNTIME_ARTIFACT_ROOTS = {
    ".openclaw",
    "AGENTS.md",
    "BOOTSTRAP.md",
    "HEARTBEAT.md",
    "IDENTITY.md",
    "SOUL.md",
    "TOOLS.md",
    "USER.md",
}
DEFAULT_GIT_AUTHOR_EMAIL = "coder-bot@local"
JOB_TOKEN_USAGE_FILENAME = "token_usage.json"
TOKEN_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


@dataclass
class WorkerContext:
    config: dict[str, Any]
    get_job: Callable[[str], sqlite3.Row | None]
    mark_job_running: Callable[[str, int], None]
    mark_job_finished: Callable[..., None]
    clear_issue_active_job: Callable[[str, int, str], None]
    ensure_issue_session: Callable[[str, int, str], sqlite3.Row]
    upsert_issue_session: Callable[..., sqlite3.Row]
    cleanup_closed_issue_if_finished: Callable[[str, int], None]
    reply_issue_execution_result_to_feishu: Callable[..., None]


def build_issue_agent_id(config: dict[str, Any], repo_full_name: str, issue_number: int) -> str:
    prefix = slugify(config.get("openclaw_session_prefix", "gh"), limit=12)
    repo_slug = slugify(repo_full_name.replace("/", "-"), limit=48)
    if prefix:
        return f"{prefix}-{repo_slug}-issue-{issue_number}"
    return f"{repo_slug}-issue-{issue_number}"


def empty_token_usage() -> dict[str, int]:
    return {key: 0 for key in TOKEN_USAGE_KEYS}


def merge_token_usage(base: dict[str, int], extra: dict[str, int] | None) -> dict[str, int]:
    if not extra:
        return base
    for key in TOKEN_USAGE_KEYS:
        base[key] = int(base.get(key) or 0) + int(extra.get(key) or 0)
    return base


def normalize_token_usage(value: dict[str, Any] | None) -> dict[str, int] | None:
    if not value:
        return None
    usage = empty_token_usage()
    for key in TOKEN_USAGE_KEYS:
        usage[key] = int(value.get(key) or 0)
    if not any(usage.values()):
        return None
    return usage


def job_token_usage_path(job_dir: Path) -> Path:
    return Path(job_dir) / JOB_TOKEN_USAGE_FILENAME


def write_job_token_usage(job_dir: Path, token_usage: dict[str, Any] | None) -> None:
    usage = normalize_token_usage(token_usage)
    if usage is None:
        return
    job_token_usage_path(job_dir).write_text(
        json.dumps(usage, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def read_job_token_usage(job_dir: Path) -> dict[str, int] | None:
    path = job_token_usage_path(job_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return normalize_token_usage(payload)


def repo_workspace_root(config: dict[str, Any], repo_full_name: str) -> Path:
    safe_name = repo_full_name.replace("/", "__")
    return ensure_dir(Path(config["repo_root"]) / safe_name)


def repo_issue_root(config: dict[str, Any], repo_full_name: str, issue_number: int) -> Path:
    return repo_workspace_root(config, repo_full_name) / "issues" / f"issue-{issue_number}"


def repo_checkout_dir(config: dict[str, Any], repo_full_name: str, issue_number: int) -> Path:
    return repo_issue_root(config, repo_full_name, issue_number) / "repo"


def openclaw_agent_registry_lock_path(config: dict[str, Any]) -> Path:
    return ensure_dir(Path(config["data_dir"]) / "openclaw") / "agents.lock"


def acquire_file_lock(target: Path) -> bool:
    try:
        fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(now_utc())
        return True
    except FileExistsError:
        return False


def wait_for_file_lock(target: Path, timeout_seconds: int) -> bool:
    deadline = time.time() + max(10, timeout_seconds)
    while True:
        if acquire_file_lock(target):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1)


def release_file_lock(target: Path) -> None:
    if target.exists():
        target.unlink()


def active_lock_path(config: dict[str, Any], repo_full_name: str, issue_number: int) -> Path:
    safe_name = repo_full_name.replace("/", "__")
    return ensure_dir(config["active_dir"]) / f"{safe_name}__{issue_number}.lock"


def repo_lock_path(config: dict[str, Any], repo_full_name: str) -> Path:
    safe_name = repo_full_name.replace("/", "__")
    return ensure_dir(config["active_dir"]) / f"{safe_name}__repo.lock"


def acquire_active_lock(config: dict[str, Any], repo_full_name: str, issue_number: int) -> bool:
    return acquire_file_lock(active_lock_path(config, repo_full_name, issue_number))


def acquire_repo_lock(config: dict[str, Any], repo_full_name: str) -> bool:
    target = repo_lock_path(config, repo_full_name)
    deadline = time.time() + max(60, config["repo_lock_wait_seconds"])
    while True:
        if acquire_file_lock(target):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(3)


def release_active_lock(config: dict[str, Any], repo_full_name: str, issue_number: int) -> None:
    target = active_lock_path(config, repo_full_name, issue_number)
    if target.exists():
        target.unlink()


def release_repo_lock(config: dict[str, Any], repo_full_name: str) -> None:
    target = repo_lock_path(config, repo_full_name)
    if target.exists():
        target.unlink()


def build_git_ssh_env(config: dict[str, Any], base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = (base_env or os.environ).copy()
    env["GIT_SSH_COMMAND"] = (
        f"ssh -i {config['github_clone_ssh_key_path']} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
    )
    return env


def build_git_commit_env(config: dict[str, Any], base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = (base_env or os.environ).copy()
    author_name = str(config["git_author_name"]).strip() or service_actor_name()
    author_email = str(config["git_author_email"]).strip() or DEFAULT_GIT_AUTHOR_EMAIL
    env["GIT_AUTHOR_NAME"] = author_name
    env["GIT_AUTHOR_EMAIL"] = author_email
    env["GIT_COMMITTER_NAME"] = author_name
    env["GIT_COMMITTER_EMAIL"] = author_email
    return env


def ensure_git_remote(cwd: Path, name: str, url: str) -> None:
    current_result = run_command(["git", "remote", "get-url", name], cwd=cwd, timeout=30)
    if current_result.returncode == 0:
        current_url = (current_result.stdout or "").strip()
        if current_url != url:
            set_result = run_command(["git", "remote", "set-url", name, url], cwd=cwd, timeout=30)
            if set_result.returncode != 0:
                raise RuntimeError(f"git remote set-url {name} failed\n{set_result.stderr}")
    else:
        add_result = run_command(["git", "remote", "add", name, url], cwd=cwd, timeout=30)
        if add_result.returncode != 0:
            raise RuntimeError(f"git remote add {name} failed\n{add_result.stderr}")

    push_result = run_command(["git", "remote", "get-url", "--push", name], cwd=cwd, timeout=30)
    current_push_url = (push_result.stdout or "").strip() if push_result.returncode == 0 else ""
    if current_push_url != url:
        set_push_result = run_command(["git", "remote", "set-url", "--push", name, url], cwd=cwd, timeout=30)
        if set_push_result.returncode != 0:
            raise RuntimeError(f"git remote set-url --push {name} failed\n{set_push_result.stderr}")


def git_remote_exists(config: dict[str, Any], remote_url: str) -> bool:
    result = run_command(
        ["git", "ls-remote", remote_url],
        cwd=ensure_dir(config["repo_root"]),
        env=build_git_ssh_env(config),
        timeout=120,
    )
    return result.returncode == 0


def ensure_fork_exists(config: dict[str, Any], upstream_owner: str, repo: str) -> None:
    fork_owner = config["github_fork_owner"]
    fork_url = f"git@github.com:{fork_owner}/{repo}.git"
    if git_remote_exists(config, fork_url):
        return

    fork_installation_id = config["github_fork_installation_id"]
    if not fork_installation_id:
        raise RuntimeError(
            f"fork {fork_owner}/{repo} does not exist or is unreachable; "
            "set GITHUB_FORK_INSTALLATION_ID for auto-fork, or create the fork manually"
        )

    fork_token = get_installation_token(config, fork_installation_id)
    existing = get_repo_info_optional(config, fork_token, fork_owner, repo)
    if existing:
        return

    create_fork(config, fork_token, upstream_owner, repo)
    deadline = time.time() + max(60, config["fork_wait_timeout_seconds"])
    while time.time() < deadline:
        info = get_repo_info_optional(config, fork_token, fork_owner, repo)
        if info and git_remote_exists(config, fork_url):
            return
        time.sleep(5)
    raise RuntimeError(f"timed out waiting for fork {fork_owner}/{repo} to become available")


def ensure_clean_worktree(repo_dir: Path) -> None:
    reset_result = run_command(["git", "reset", "--hard"], cwd=repo_dir, timeout=60)
    if reset_result.returncode != 0:
        raise RuntimeError(f"git reset failed\n{reset_result.stderr}")

    clean_result = run_command(["git", "clean", "-fdx"], cwd=repo_dir, timeout=120)
    if clean_result.returncode != 0:
        raise RuntimeError(f"git clean failed\n{clean_result.stderr}")


def git_fetch_remote(config: dict[str, Any], repo_dir: Path, remote: str, *, label: str) -> None:
    fetch_result = run_command(
        ["git", "fetch", "--prune", remote],
        cwd=repo_dir,
        env=build_git_ssh_env(config),
        timeout=300,
    )
    if fetch_result.returncode != 0:
        raise RuntimeError(f"git fetch {label} failed\n{fetch_result.stderr}")


def git_checkout_branch(repo_dir: Path, branch_name: str, start_point: str, *, label: str) -> None:
    checkout_result = run_command(
        ["git", "checkout", "-B", branch_name, start_point],
        cwd=repo_dir,
        timeout=60,
    )
    if checkout_result.returncode != 0:
        raise RuntimeError(f"git checkout {label} failed\n{checkout_result.stderr}")


def git_ref_exists(repo_dir: Path, ref_name: str) -> bool:
    result = run_command(
        ["git", "rev-parse", "--verify", "--quiet", ref_name],
        cwd=repo_dir,
        timeout=30,
    )
    return result.returncode == 0


def git_current_branch(repo_dir: Path) -> str:
    result = run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_dir,
        timeout=30,
    )
    branch = (result.stdout or "").strip() if result.returncode == 0 else ""
    if not branch:
        raise RuntimeError("could not determine current branch for PR publish")
    return branch


def git_status_entries(repo_dir: Path) -> list[tuple[str, str]]:
    result = run_command(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=repo_dir,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git status failed\n{result.stderr}")

    entries: list[tuple[str, str]] = []
    for raw_line in (result.stdout or "").splitlines():
        if len(raw_line) < 4:
            continue
        entries.append((raw_line[:2], raw_line[3:]))
    return entries


def openclaw_runtime_artifact_path(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/").strip()
    if not normalized:
        return False
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return False
    root = parts[0]
    if root == ".openclaw":
        return True
    return len(parts) == 1 and root in OPENCLAW_RUNTIME_ARTIFACT_ROOTS


def remove_repo_path(target: Path) -> None:
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
        return
    if target.exists() or target.is_symlink():
        target.unlink()


def cleanup_openclaw_runtime_artifacts(repo_dir: Path) -> list[str]:
    removed: list[str] = []
    for status, relative_path in git_status_entries(repo_dir):
        if status != "??" or not openclaw_runtime_artifact_path(relative_path):
            continue
        target = repo_dir / relative_path
        if not target.exists() and not target.is_symlink():
            continue
        remove_repo_path(target)
        removed.append(relative_path)
    return removed


def build_issue_commit_message(issue_number: int, issue_title: str) -> str:
    title = " ".join((issue_title or "").split()).strip()
    if title:
        return f"{service_actor_name()}: resolve issue #{issue_number} {title}"
    return f"{service_actor_name()}: resolve issue #{issue_number}"


def commit_repo_changes(
    config: dict[str, Any],
    repo_dir: Path,
    issue_number: int,
    issue_title: str,
) -> str:
    removed_artifacts = cleanup_openclaw_runtime_artifacts(repo_dir)
    if removed_artifacts:
        print(
            "removed OpenClaw runtime artifacts before commit: "
            + ", ".join(sorted(removed_artifacts))
        )

    add_result = run_command(
        ["git", "add", "-A", "--", "."],
        cwd=repo_dir,
        timeout=120,
    )
    if add_result.returncode != 0:
        raise RuntimeError(f"git add failed\n{tail_text(add_result.stderr or add_result.stdout, 3000)}")

    cached_diff = run_command(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_dir,
        timeout=60,
    )
    if cached_diff.returncode == 0:
        raise RuntimeError("executor reported succeeded but produced no commit-worthy repository changes")
    if cached_diff.returncode != 1:
        raise RuntimeError(
            "git diff --cached failed\n"
            f"{tail_text(cached_diff.stderr or cached_diff.stdout, 3000)}"
        )

    commit_result = run_command(
        ["git", "commit", "-m", build_issue_commit_message(issue_number, issue_title)],
        cwd=repo_dir,
        env=build_git_commit_env(config),
        timeout=180,
    )
    if commit_result.returncode != 0:
        raise RuntimeError(
            "git commit failed\n"
            f"{tail_text(commit_result.stderr or commit_result.stdout, 3000)}"
        )

    rev_result = run_command(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        timeout=30,
    )
    commit_sha = (rev_result.stdout or "").strip() if rev_result.returncode == 0 else ""
    if not commit_sha:
        raise RuntimeError("git commit completed but HEAD sha could not be determined")
    return commit_sha


def ensure_repo_checkout(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    default_branch: str,
    branch_name: str,
) -> tuple[Path, Path]:
    upstream_owner, repo = repo_full_name.split("/", 1)
    fork_owner = config["github_fork_owner"]
    workspace_root = ensure_dir(repo_issue_root(config, repo_full_name, issue_number))
    repo_dir = repo_checkout_dir(config, repo_full_name, issue_number)
    fork_url = f"git@github.com:{fork_owner}/{repo}.git"
    upstream_url = f"git@github.com:{upstream_owner}/{repo}.git"

    ensure_fork_exists(config, upstream_owner, repo)

    if not (repo_dir / ".git").exists():
        clone_result = run_command(
            ["git", "clone", fork_url, str(repo_dir)],
            cwd=workspace_root,
            env=build_git_ssh_env(config),
            timeout=300,
        )
        if clone_result.returncode != 0:
            raise RuntimeError(f"git clone failed\n{clone_result.stderr}")

    ensure_git_remote(repo_dir, "origin", fork_url)
    ensure_git_remote(repo_dir, "upstream", upstream_url)
    ensure_clean_worktree(repo_dir)
    git_fetch_remote(config, repo_dir, "origin", label="origin")
    git_fetch_remote(config, repo_dir, "upstream", label="upstream")

    sync_script = repo_dir / config["sync_script_path"]
    if sync_script.is_file():
        git_checkout_branch(repo_dir, default_branch, f"origin/{default_branch}", label="base")

        sync_env = build_git_ssh_env(config)
        sync_env["AUTO_PUSH"] = "true"
        sync_result = run_command(
            ["bash", str(sync_script)],
            cwd=repo_dir,
            env=sync_env,
            timeout=600,
        )
        combined_sync_output = "\n".join(part for part in [sync_result.stdout, sync_result.stderr] if part)
        if sync_result.returncode != 0:
            raise RuntimeError(f"sync.sh failed\n{tail_text(combined_sync_output, 3000)}")

        git_fetch_remote(config, repo_dir, "origin", label="origin after sync")
        branch_start = (
            f"origin/{branch_name}"
            if git_ref_exists(repo_dir, f"refs/remotes/origin/{branch_name}")
            else f"origin/{default_branch}"
        )
        git_checkout_branch(repo_dir, branch_name, branch_start, label="branch")
        return workspace_root, repo_dir

    git_checkout_branch(repo_dir, default_branch, f"upstream/{default_branch}", label="base")
    branch_start = (
        f"origin/{branch_name}"
        if git_ref_exists(repo_dir, f"refs/remotes/origin/{branch_name}")
        else f"upstream/{default_branch}"
    )
    git_checkout_branch(repo_dir, branch_name, branch_start, label="branch")

    return workspace_root, repo_dir


def publish_pull_request_via_git_push_and_api(
    config: dict[str, Any],
    token: str,
    work_dir: Path,
    upstream_owner: str,
    repo: str,
    issue_number: int,
    issue_title: str,
    base_branch: str,
    pr_title: str,
    pr_body: str,
) -> dict[str, Any]:
    current_branch = git_current_branch(work_dir)
    commit_sha = commit_repo_changes(config, work_dir, issue_number, issue_title)
    head_ref = f"{config['github_fork_owner']}:{current_branch}"

    push_result = run_command(
        ["git", "push", "--set-upstream", "origin", current_branch],
        cwd=work_dir,
        env=build_git_ssh_env(config),
        timeout=300,
    )
    combined_push_output = "\n".join(part for part in [push_result.stdout, push_result.stderr] if part)
    if push_result.returncode != 0:
        raise RuntimeError(f"git push failed\n{tail_text(combined_push_output, 3000)}")

    existing = list_pull_requests(
        config,
        token,
        upstream_owner,
        repo,
        head=head_ref,
        base=base_branch,
    )
    if existing:
        return {"html_url": existing[0]["html_url"], "method": "git+api", "commit_sha": commit_sha}
    pr = create_pull_request(
        config,
        token,
        upstream_owner,
        repo,
        title=pr_title,
        body=pr_body,
        head=head_ref,
        base=base_branch,
    )
    return {"html_url": pr["html_url"], "method": "git+api", "commit_sha": commit_sha}


def build_prompt(
    repo_full_name: str,
    issue: dict[str, Any],
    repo_path: str,
    test_command: str,
    *,
    session_key: str | None = None,
) -> str:
    issue_body = issue.get("body") or "(no issue body)"
    lines = [
        f"你正在处理 GitHub Issue #{issue['number']}。",
        f"仓库：{repo_full_name}",
        f"本地仓库路径：{repo_path}",
        f"Issue 标题：{issue['title']}",
    ]
    if session_key:
        lines.append(f"会话标识：{session_key}")
    lines.extend(
        [
            "",
            "执行阶段说明：",
            "- 飞书线程里已经收到明确的 `/run` 执行确认，现在就是正式执行阶段。",
            "- 不要再等待新的确认消息，不要因为“缺少 `/run`”返回 `needs_human`。",
            "- 如果历史上下文里还保留着讨论阶段的约束，以当前这条执行指令为准。",
            "",
            "工作区说明：",
            f"- 当前真正的 Git 仓库根目录只有：{repo_path}",
            "- 你的会话工作区可能比仓库根目录更大，但只有上面这个 repo 路径里的改动才会被提交。",
            "- 所有文件读写、编辑、检查都必须明确针对这个 repo 路径，不要把文件写到它的父目录、兄弟目录或其他工作区位置。",
            "- 如果你要新增文件，请直接写到这个 repo 路径下的目标位置。",
            "- 不要只根据推断声称“文件已经创建”或“修改已经完成”；必须用工具实际创建并再次读取验证。",
            "",
            "目标：",
            "- 只解决当前这个 issue。",
            "- 只在当前仓库内工作。",
            "- 采用最小化修改方案，不做无关重构。",
            "",
            "硬性限制：",
            "- 不要执行 git commit、git push、创建 PR、调用 sync.sh。",
            "- 不要修改 CI、部署配置、发布脚本、基础设施配置，除非 issue 明确要求且不改就无法解决。",
            "- 不要新增无关文档、示例文件、演示代码。",
            "- 不要修改其他仓库或访问当前仓库之外的路径。",
            "",
            "说明：",
            "- 这是同一个 Issue 的持续会话；如果之前已经分析过，请沿用已有结论，避免重复工作。",
            "- 你只负责修改工作区内容和必要的最小验证。",
            "- git commit、git push、创建 PR 会由外层机器人在你返回 `result: succeeded` 后自动执行。",
            "- 即使 Issue 正文要求“提交代码并创建 PR”，你也不要自己执行这些步骤，只要把代码改好并返回 `result: succeeded`。",
            "",
            "执行要求：",
            "1. 先阅读相关代码和 Issue 内容，再决定修改点。",
            "2. 优先复用现有实现和现有代码风格。",
            "3. 如果信息不足，做保守实现，并在最终结果里明确说明假设。",
            "4. 完成后运行最小必要验证。",
        ]
    )
    if test_command:
        lines.append(f"5. 如果配置了测试命令，执行：{test_command}")
    else:
        lines.append("5. 当前没有配置自动测试命令，至少做与你改动直接相关的最小自检。")
    lines.extend(
        [
            "6. 外层机器人会在你返回后检查 repo 内是否真的产生了可提交改动；不要把 `git status`、`gh issue view` 或其他 shell 命令当成继续任务的前置条件。",
            "7. 如果 shell/exec 工具不可用，继续使用可直接读写文件的工具完成修改和验证，不要因此中断。",
            f"8. 在输出 `result: succeeded` 前，必须再次读取你改动后的目标文件，确认它确实位于 `{repo_path}` 下且内容正确。",
            f"9. 如果你没有真正把改动落到 `{repo_path}` 里，绝对不允许输出 `result: succeeded`。",
            "",
            "最终只输出以下结构，不要输出其他无关内容：",
            "",
            "result: succeeded | no_change | needs_human",
            "",
            "summary:",
            "- 变更点1",
            "- 变更点2",
            "",
            "tests:",
            "- 执行的验证1",
            "- 执行的验证2",
            "",
            "risks:",
            "- 剩余风险1",
            "- 剩余风险2",
            "",
            "Issue 正文：",
            issue_body,
        ]
    )
    return "\n".join(lines).strip()


def build_missing_changes_retry_prompt(repo_path: str, previous_result: str) -> str:
    return "\n".join(
        [
            "上一轮你已经返回 `result: succeeded`，但外层检查发现 Git 仓库里仍然没有任何可提交改动。",
            "",
            f"真实 Git 仓库根目录：{repo_path}",
            "",
            "这通常意味着：",
            "- 你只描述了修改，但没有真正落文件；或者",
            "- 你把文件写到了仓库目录之外；或者",
            "- 你验证了错误的路径；或者",
            "- 你把 shell 工具失败误当成了任务已经完成。",
            "",
            "现在请立即修正：",
            f"1. 只在 `{repo_path}` 下真正落地所需修改。",
            "2. 优先使用可直接读写文件的工具，不要把 `git status`、`gh issue view` 或其他 shell 命令当成前置条件。",
            "3. 再次读取你修改后的目标文件，确认内容准确且路径正确。",
            "4. 外层会再次检查 repo 是否真的有改动；只有你确认文件已经实际写入后，才能输出 `result: succeeded`。",
            "5. 如果仍然无法在 repo 内产生改动，就输出 `result: needs_human` 并明确说明原因。",
            "",
            "你上一轮的回答如下：",
            previous_result.strip() or "(empty)",
            "",
            "请重新执行，并且最终仍然只输出约定的 result/summary/tests/risks 结构。",
        ]
    ).strip()


def parse_executor_result(text: str) -> dict[str, str]:
    raw = (text or "").strip()
    if not raw:
        raise RuntimeError("executor returned empty final response")
    match = re.search(r"(?mi)^\s*result:\s*(succeeded|no_change|needs_human)\s*$", raw)
    if not match:
        raise RuntimeError("executor final response missing `result:` line")
    return {"status": match.group(1), "text": raw}


def summarize_openclaw_turn_failure(response_text: str, stderr_text: str) -> str | None:
    response = (response_text or "").strip()
    stderr = (stderr_text or "").strip()
    response_lower = response.lower()
    stderr_lower = stderr.lower()

    if (
        "agent couldn't generate a response" not in response_lower
        and "incomplete turn detected" not in stderr_lower
    ):
        return None

    interesting_lines: list[str] = []
    for line in stderr.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if (
            "exec failed" in lowered
            or "incomplete turn detected" in lowered
            or "gateway connect failed" in lowered
            or "denied" in lowered
        ):
            interesting_lines.append(stripped)

    if not interesting_lines and stderr:
        interesting_lines.append(tail_text(stderr, 1500))

    parts = ["openclaw turn failed before producing a structured final response"]
    if response:
        parts.extend(["assistant reply:", tail_text(response, 600)])
    if interesting_lines:
        parts.extend(["stderr:", tail_text("\n".join(interesting_lines), 1800)])
    return "\n".join(parts).strip()


def parse_json_document(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise RuntimeError("empty json payload")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("json payload not found in executor output") from None
        payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise RuntimeError("executor json payload is not an object")
    return payload


def repo_has_commit_worthy_changes(repo_dir: Path) -> bool:
    for status, relative_path in git_status_entries(repo_dir):
        if status == "??" and openclaw_runtime_artifact_path(relative_path):
            continue
        return True
    return False


def ensure_openclaw_issue_agent(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    work_dir: Path,
) -> str:
    agent_id = build_issue_agent_id(config, repo_full_name, issue_number)
    agent_dir = Path(config["openclaw_state_dir"]) / "agents" / agent_id / "agent"
    lock_path = openclaw_agent_registry_lock_path(config)
    if not wait_for_file_lock(lock_path, 180):
        raise RuntimeError("timed out waiting for OpenClaw agent registry lock")

    try:
        agents = list_openclaw_agents(config)
        existing = next((item for item in agents if str(item.get("id") or "") == agent_id), None)
        expected_workspace = str(work_dir.resolve(strict=False))
        expected_model = config["openclaw_model"]
        if existing:
            current_workspace = str(existing.get("workspace") or "")
            current_model = str(existing.get("model") or "")
            if current_workspace == expected_workspace and current_model == expected_model:
                return agent_id
            delete_result = run_command(
                [config["openclaw_bin"], "agents", "delete", "--force", agent_id],
                cwd=Path(config["app_home"]),
                env=build_openclaw_env(config),
                timeout=120,
            )
            if delete_result.returncode != 0:
                raise RuntimeError(
                    "openclaw agents delete failed\n"
                    f"{tail_text(delete_result.stderr or delete_result.stdout, 3000)}"
                )

        add_result = run_command(
            [
                config["openclaw_bin"],
                "agents",
                "add",
                "--json",
                "--non-interactive",
                "--workspace",
                expected_workspace,
                "--agent-dir",
                str(agent_dir),
                "--model",
                expected_model,
                agent_id,
            ],
            cwd=Path(config["app_home"]),
            env=build_openclaw_env(config),
            timeout=120,
        )
        if add_result.returncode != 0:
            raise RuntimeError(
                "openclaw agents add failed\n"
                f"{tail_text(add_result.stderr or add_result.stdout, 3000)}"
            )
        return agent_id
    finally:
        release_file_lock(lock_path)


def delete_openclaw_issue_agent(config: dict[str, Any], repo_full_name: str, issue_number: int) -> None:
    agent_id = build_issue_agent_id(config, repo_full_name, issue_number)
    lock_path = openclaw_agent_registry_lock_path(config)
    if not wait_for_file_lock(lock_path, 60):
        print(f"warning: timed out waiting for OpenClaw agent registry lock while deleting {agent_id}")
        return
    try:
        delete_result = run_command(
            [config["openclaw_bin"], "agents", "delete", "--force", agent_id],
            cwd=Path(config["app_home"]),
            env=build_openclaw_env(config),
            timeout=120,
        )
        delete_output = "\n".join(part for part in [delete_result.stdout, delete_result.stderr] if part)
        if delete_result.returncode != 0 and "not found" not in delete_output.lower():
            print(
                "warning: openclaw agents delete failed for "
                f"{agent_id}: {tail_text(delete_output, 1000)}"
            )
    finally:
        release_file_lock(lock_path)


def extract_openclaw_result(payload: dict[str, Any]) -> tuple[str, str | None]:
    meta = payload.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    text = str(meta.get("finalAssistantVisibleText") or "").strip()
    if not text:
        parts: list[str] = []
        raw_payloads = payload.get("payloads") or []
        if isinstance(raw_payloads, list):
            for item in raw_payloads:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("text") or "").strip()
                if content:
                    parts.append(content)
        text = "\n\n".join(parts).strip()
    if not text:
        raise RuntimeError("openclaw returned no assistant text")

    agent_meta = meta.get("agentMeta") or {}
    if not isinstance(agent_meta, dict):
        agent_meta = {}
    session_id = str(agent_meta.get("sessionId") or "").strip() or None
    if not session_id:
        cli_binding = agent_meta.get("cliSessionBinding") or {}
        if isinstance(cli_binding, dict):
            session_id = str(cli_binding.get("sessionId") or "").strip() or None
    return text, session_id


def run_codex_chat_turn(
    config: dict[str, Any],
    work_dir: Path,
    prompt: str,
    *,
    resume_session_id: str | None = None,
    log_dir: Path | None = None,
    selected_model: str | None = None,
) -> dict[str, Any]:
    env = build_codex_env(config)
    temp_root = ensure_dir(Path(config["data_dir"]) / "codex" / "tmp")
    output_path = (log_dir or temp_root) / f"codex-last-message-{int(time.time() * 1000)}-{os.getpid()}.txt"
    command = [
        config["codex_bin"],
        "exec",
    ]
    if resume_session_id:
        command.append("resume")
    command.extend(
        [
            "--json",
            "-o",
            str(output_path),
            "--skip-git-repo-check",
        ]
    )
    if not resume_session_id:
        command.extend(["-C", str(work_dir)])
    effective_model = str(selected_model or config.get("codex_model") or "").strip()
    if effective_model:
        command.extend(["-m", effective_model])
    if config.get("codex_use_dangerously_bypass", True):
        command.append("--dangerously-bypass-approvals-and-sandbox")
    if resume_session_id:
        command.append(str(resume_session_id))
    command.append(prompt)

    result = run_command(
        command,
        cwd=work_dir,
        env=env,
        timeout=int(config["codex_timeout"]) + 120,
    )
    if log_dir is not None:
        (log_dir / "codex.stdout.jsonl").write_text(result.stdout or "", encoding="utf-8")
        (log_dir / "codex.stderr.log").write_text(result.stderr or "", encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            "codex execution failed\n"
            f"{tail_text(result.stderr or result.stdout, 4000)}"
        )

    thread_id, streamed_text = parse_codex_jsonl_events(result.stdout or "")
    response_text = ""
    if output_path.exists():
        response_text = output_path.read_text(encoding="utf-8").strip()
    if not response_text:
        response_text = streamed_text or ""
    response_text = response_text.strip()
    if not response_text:
        raise RuntimeError("codex returned empty assistant reply")
    return {
        "agent_id": "codex",
        "agent_session_id": thread_id or resume_session_id,
        "text": response_text,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
        "token_usage": parse_codex_jsonl_usage(result.stdout or ""),
    }


def run_openclaw_chat_turn(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    prompt: str,
    session_key: str,
    *,
    log_dir: Path | None = None,
) -> dict[str, Any]:
    agent_work_dir = openclaw_issue_workspace_dir(config, repo_full_name, issue_number)
    agent_id = ensure_openclaw_issue_agent(config, repo_full_name, issue_number, agent_work_dir)
    env = build_openclaw_env(config)

    command = [
        config["openclaw_bin"],
        "agent",
        "--local",
        "--json",
        "--agent",
        agent_id,
        "--session-id",
        session_key,
        "--model",
        config["openclaw_model"],
        "--timeout",
        str(config["openclaw_timeout"]),
        "--message",
        prompt,
    ]
    result = run_command(
        command,
        cwd=agent_work_dir,
        env=env,
        timeout=config["openclaw_timeout"] + 120,
    )
    if log_dir is not None:
        (log_dir / "openclaw.stdout.log").write_text(result.stdout or "", encoding="utf-8")
        (log_dir / "openclaw.stderr.log").write_text(result.stderr or "", encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            "openclaw execution failed\n"
            f"{tail_text(result.stderr or result.stdout, 4000)}"
        )

    payload = parse_json_document(result.stdout or "")
    if log_dir is not None:
        (log_dir / "openclaw.response.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    response_text, agent_session_id = extract_openclaw_result(payload)
    if not response_text.strip():
        raise RuntimeError(f"openclaw returned empty discussion reply for agent {agent_id}")
    return {
        "agent_id": agent_id,
        "agent_session_id": agent_session_id or session_key,
        "text": response_text,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
        "payload": payload,
    }


def run_openclaw(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    work_dir: Path,
    prompt: str,
    job_dir: Path,
    session_key: str,
) -> dict[str, str]:
    del work_dir
    turn = run_openclaw_chat_turn(
        config,
        repo_full_name,
        issue_number,
        prompt,
        session_key,
        log_dir=job_dir,
    )
    response_text = str(turn["text"])
    stderr_text = str(turn.get("stderr") or "")
    failure_summary = summarize_openclaw_turn_failure(response_text, stderr_text)
    if failure_summary:
        raise RuntimeError(failure_summary)
    try:
        parsed = parse_executor_result(response_text)
    except RuntimeError as exc:
        parts = [
            "openclaw final response missing structured result",
            "assistant reply:",
            tail_text(response_text, 1200),
        ]
        if stderr_text.strip():
            parts.extend(["stderr:", tail_text(stderr_text, 1200)])
        raise RuntimeError("\n".join(parts).strip()) from exc
    parsed["agent_id"] = str(turn["agent_id"])
    parsed["agent_session_id"] = str(turn["agent_session_id"] or session_key)
    return parsed


def run_discussion_turn(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    work_dir: Path,
    prompt: str,
    session_key: str,
    *,
    agent_session_id: str | None = None,
    log_dir: Path | None = None,
    selected_model: str | None = None,
) -> dict[str, Any]:
    backend = str(config.get("execution_backend") or "openclaw").strip().lower()
    if backend == "codex":
        return run_codex_chat_turn(
            config,
            work_dir,
            prompt,
            resume_session_id=agent_session_id,
            log_dir=log_dir,
            selected_model=selected_model,
        )
    return run_openclaw_chat_turn(
        config,
        repo_full_name,
        issue_number,
        prompt,
        session_key,
        log_dir=log_dir,
    )


def run_executor(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    work_dir: Path,
    prompt: str,
    job_dir: Path,
    session_key: str,
    agent_session_id: str | None = None,
    selected_model: str | None = None,
) -> dict[str, str]:
    backend = str(config.get("execution_backend") or "openclaw").strip().lower()
    if backend == "codex":
        turn = run_codex_chat_turn(
            config,
            work_dir,
            prompt,
            resume_session_id=agent_session_id,
            log_dir=job_dir,
            selected_model=selected_model,
        )
        response_text = str(turn["text"])
        stderr_text = str(turn.get("stderr") or "")
        try:
            parsed = parse_executor_result(response_text)
        except RuntimeError as exc:
            parts = [
                "codex final response missing structured result",
                "assistant reply:",
                tail_text(response_text, 1200),
            ]
            if stderr_text.strip():
                parts.extend(["stderr:", tail_text(stderr_text, 1200)])
            raise RuntimeError("\n".join(parts).strip()) from exc
        parsed["agent_id"] = str(turn["agent_id"])
        parsed["agent_session_id"] = str(turn["agent_session_id"] or agent_session_id or session_key)
        parsed["token_usage"] = normalize_token_usage(turn.get("token_usage"))
        return parsed
    return run_openclaw(config, repo_full_name, issue_number, work_dir, prompt, job_dir, session_key)


def process_job(context: WorkerContext, job_id: str) -> None:
    row = context.get_job(job_id)
    if row is None:
        raise RuntimeError(f"job not found: {job_id}")
    context.mark_job_running(job_id, os.getpid())

    payload = json.loads(str(row["payload_json"]))
    repo_full_name = str(row["repo_full_name"])
    issue_number = int(row["issue_number"])
    issue = payload["issue"]
    issue_title = issue.get("title") or f"Issue #{issue_number}"
    owner, repo = repo_full_name.split("/", 1)
    job_dir = Path(str(row["job_dir"]))

    final_status = "failed"
    error_text: str | None = None
    result_summary: str | None = None
    pr_url: str | None = None
    token_usage = empty_token_usage()
    active_locked = False
    repo_locked = False

    try:
        active_locked = acquire_active_lock(context.config, repo_full_name, issue_number)
        if not active_locked:
            raise RuntimeError(f"issue already active: {repo_full_name}#{issue_number}")

        repo_locked = acquire_repo_lock(context.config, repo_full_name)
        if not repo_locked:
            raise RuntimeError(f"repo lock timeout: {repo_full_name}")

        token = get_installation_token(context.config)
        repo_info = get_repo_info(context.config, token, owner, repo)
        default_branch = context.config["default_base_branch"] or str(repo_info.get("default_branch") or "main")

        session_row = context.ensure_issue_session(repo_full_name, issue_number, issue_title)
        session_key = str(session_row["session_key"])
        branch_name = str(session_row["branch_name"])
        existing_agent_session_id = str(session_row["agent_session_id"] or "").strip() or None
        selected_model = str(session_row["selected_model"] or "").strip() or None
        _, work_dir = ensure_repo_checkout(context.config, repo_full_name, issue_number, default_branch, branch_name)

        prompt = build_prompt(
            repo_full_name,
            issue,
            str(work_dir),
            context.config["test_command"],
            session_key=session_key,
        )
        executor_result = run_executor(
            context.config,
            repo_full_name,
            issue_number,
            work_dir,
            prompt,
            job_dir,
            session_key,
            existing_agent_session_id,
            selected_model,
        )
        final_status = str(executor_result["status"])
        result_summary = str(executor_result["text"])
        agent_session_id = str(executor_result.get("agent_session_id") or "").strip() or None
        merge_token_usage(token_usage, normalize_token_usage(executor_result.get("token_usage")))

        context.upsert_issue_session(
            repo_full_name,
            issue_number,
            session_state="active",
            agent_session_id=agent_session_id,
            summary=result_summary,
            last_result_status=final_status,
        )

        if final_status == "succeeded":
            if not repo_has_commit_worthy_changes(work_dir):
                retry_prompt = build_missing_changes_retry_prompt(str(work_dir), result_summary)
                retry_job_dir = ensure_dir(job_dir / "retry-no-diff")
                retry_result = run_executor(
                    context.config,
                    repo_full_name,
                    issue_number,
                    work_dir,
                    retry_prompt,
                    retry_job_dir,
                    session_key,
                    agent_session_id,
                    selected_model,
                )
                final_status = str(retry_result["status"])
                result_summary = str(retry_result["text"])
                agent_session_id = str(retry_result.get("agent_session_id") or "").strip() or agent_session_id
                merge_token_usage(token_usage, normalize_token_usage(retry_result.get("token_usage")))
                context.upsert_issue_session(
                    repo_full_name,
                    issue_number,
                    session_state="active",
                    agent_session_id=agent_session_id,
                    summary=result_summary,
                    last_result_status=final_status,
                )
                if final_status == "succeeded" and not repo_has_commit_worthy_changes(work_dir):
                    raise RuntimeError(
                        "executor claimed succeeded but repository still has no commit-worthy changes after retry"
                    )

        if final_status == "succeeded":
            pr_title = f"{context.config['pr_title_prefix']} {issue_title}".strip()
            publish_result = publish_pull_request_via_git_push_and_api(
                context.config,
                token,
                work_dir,
                owner,
                repo,
                issue_number,
                issue_title,
                default_branch,
                pr_title,
                result_summary,
            )
            pr_url = str(publish_result["html_url"])
            context.upsert_issue_session(
                repo_full_name,
                issue_number,
                session_state="done",
                agent_session_id=agent_session_id,
                pr_url=pr_url,
                summary=result_summary,
                last_result_status=final_status,
            )
            comment_lines = [
                f"{service_actor_name()} 已创建 PR：{pr_url}",
                "",
                result_summary,
            ]
            if context.config["submit_comment_after_pr"] and context.config["submit_comment_body"]:
                comment_lines.extend(["", context.config["submit_comment_body"]])
            comment_issue(context.config, token, owner, repo, issue_number, "\n".join(comment_lines).strip())
            result_summary = f"{result_summary}\n\nPR: {pr_url}"
        elif final_status == "no_change":
            context.upsert_issue_session(
                repo_full_name,
                issue_number,
                session_state="done",
                agent_session_id=agent_session_id,
                summary=result_summary,
                last_result_status=final_status,
            )
        else:
            context.upsert_issue_session(
                repo_full_name,
                issue_number,
                session_state="failed",
                agent_session_id=agent_session_id,
                summary=result_summary,
                last_result_status=final_status,
            )
    except Exception:
        error_text = tail_text(traceback.format_exc(), 4000)
        final_status = "failed"
        context.upsert_issue_session(
            repo_full_name,
            issue_number,
            session_state="failed",
            summary=error_text,
            last_result_status="failed",
        )
        raise
    finally:
        final_token_usage = normalize_token_usage(token_usage)
        write_job_token_usage(job_dir, final_token_usage)
        if final_token_usage is not None:
            print(f"[worker] job {job_id} token usage: {json.dumps(final_token_usage, ensure_ascii=True, sort_keys=True)}")
        context.mark_job_finished(
            job_id,
            final_status,
            error_text=error_text,
            result_summary=result_summary,
        )
        context.clear_issue_active_job(repo_full_name, issue_number, job_id)
        if active_locked:
            release_active_lock(context.config, repo_full_name, issue_number)
        if repo_locked:
            release_repo_lock(context.config, repo_full_name)
        context.cleanup_closed_issue_if_finished(repo_full_name, issue_number)
        context.reply_issue_execution_result_to_feishu(
            repo_full_name,
            issue_number,
            job_id=job_id,
            status=final_status,
            pr_url=pr_url,
            result_summary=result_summary,
            error_text=error_text,
            token_usage=final_token_usage,
        )


def spawn_worker(config: dict[str, Any], job_id: str, job_dir: Path) -> int:
    log_file = job_dir / "worker.log"
    with log_file.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "src",
                "--env-file",
                config["env_file"],
                "run-job",
                job_id,
            ],
            stdout=handle,
            stderr=handle,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return process.pid


__all__ = [
    "WorkerContext",
    "acquire_active_lock",
    "acquire_repo_lock",
    "active_lock_path",
    "build_issue_agent_id",
    "build_missing_changes_retry_prompt",
    "build_prompt",
    "delete_openclaw_issue_agent",
    "ensure_openclaw_issue_agent",
    "ensure_repo_checkout",
    "publish_pull_request_via_git_push_and_api",
    "release_active_lock",
    "release_repo_lock",
    "repo_has_commit_worthy_changes",
    "repo_checkout_dir",
    "repo_issue_root",
    "repo_lock_path",
    "process_job",
    "run_executor",
    "run_openclaw_chat_turn",
    "spawn_worker",
]
