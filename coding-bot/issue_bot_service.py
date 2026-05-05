#!/usr/bin/env python3
import argparse
import base64
import hashlib
import hmac
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from flask import Flask, jsonify, request


APP_DIR = Path(__file__).resolve().parent
DEFAULT_FORK_OWNER = "YeYing2025"
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "no_change", "needs_human", "cancelled"}
ACTIVE_JOB_STATUSES = {"queued", "running"}

CONFIG: dict[str, Any] = {}
APP = Flask(__name__)
STATE_LOCK = threading.Lock()
DB_LOCK = threading.Lock()
RUNTIME: dict[str, Any] = {
    "started_at": None,
    "last_poll_started_at": None,
    "last_poll_completed_at": None,
    "last_poll_error": None,
    "last_queued_job_id": None,
    "last_dispatched_job_id": None,
}
SERVICE_BOOT_LOCK = threading.Lock()
SERVICE_BOOTSTRAPPED = False
DISPATCH_THREAD: threading.Thread | None = None
POLLING_THREAD: threading.Thread | None = None


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def normalize_codex_model(value: str) -> str:
    model = value.strip()
    legacy_prefix = "codex-cli/"
    if model.startswith(legacy_prefix):
        model = model[len(legacy_prefix) :]
    if model == "gpt-5.3-codex":
        return "gpt-5.4"
    return model


def resolve_path_value(value: str, *, base_dir: Path) -> Path:
    target = Path(value).expanduser()
    if not target.is_absolute():
        target = base_dir / target
    return target.resolve(strict=False)


def env_path(
    name: str,
    default: Path,
    *,
    base_dir: Path,
) -> str:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return str(default.resolve(strict=False))
    return str(resolve_path_value(raw.strip(), base_dir=base_dir))


def env_command(name: str, default: str, *, base_dir: Path) -> str:
    raw = os.getenv(name)
    value = (raw if raw is not None else default).strip()
    if "/" in value:
        return str(resolve_path_value(value, base_dir=base_dir))
    return value


def read_config(env_file_path: Path | None = None) -> dict[str, Any]:
    env_base_dir = (env_file_path or APP_DIR / ".env").expanduser().resolve(strict=False).parent
    app_home = resolve_path_value(os.getenv("APP_HOME", "."), base_dir=env_base_dir)
    data_dir = resolve_path_value(os.getenv("DATA_DIR", "data"), base_dir=app_home)
    secrets_dir = resolve_path_value(os.getenv("SECRETS_DIR", "secrets"), base_dir=app_home)
    allowed_repos = [
        item.strip()
        for item in os.getenv("ALLOWED_REPOS", "").split(",")
        if item.strip()
    ]
    if not allowed_repos:
        owner = os.getenv("GITHUB_OWNER", "").strip()
        repo = os.getenv("GITHUB_REPO", "").strip()
        if owner and repo:
            allowed_repos = [f"{owner}/{repo}"]

    return {
        "app_home": str(app_home),
        "data_dir": str(data_dir),
        "secrets_dir": str(secrets_dir),
        "listen_host": os.getenv("LISTEN_HOST", "0.0.0.0"),
        "listen_port": env_int("LISTEN_PORT", 9081),
        "webhook_enabled": env_bool("ENABLE_WEBHOOK", False),
        "webhook_secret": os.getenv("GITHUB_WEBHOOK_SECRET", ""),
        "github_api_url": os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
        "github_app_id": os.getenv("GITHUB_APP_ID", "").strip(),
        "github_installation_id": os.getenv("GITHUB_INSTALLATION_ID", "").strip(),
        "github_fork_installation_id": os.getenv("GITHUB_FORK_INSTALLATION_ID", "").strip(),
        "github_private_key_path": env_path(
            "GITHUB_PRIVATE_KEY_PATH",
            secrets_dir / "github-app.pem",
            base_dir=app_home,
        ),
        "github_clone_mode": os.getenv("GITHUB_CLONE_MODE", "ssh").strip(),
        "github_clone_ssh_key_path": env_path(
            "GITHUB_CLONE_SSH_KEY_PATH",
            secrets_dir / "github-push-key",
            base_dir=app_home,
        ),
        "github_fork_owner": os.getenv("GITHUB_FORK_OWNER", DEFAULT_FORK_OWNER).strip()
        or DEFAULT_FORK_OWNER,
        "allowed_repos": allowed_repos,
        "run_on_issue_opened": env_bool("RUN_ON_ISSUE_OPENED", False),
        "trigger_label": os.getenv("TRIGGER_LABEL", "ai-run").strip(),
        "trigger_comment": os.getenv("TRIGGER_COMMENT", "/run").strip(),
        "codex_bin": env_command("CODEX_BIN", "codex", base_dir=app_home),
        "codex_model": normalize_codex_model(os.getenv("CODEX_MODEL", "gpt-5.4")),
        "codex_timeout": env_int("CODEX_TIMEOUT", 1800),
        "codex_source_home": env_path(
            "CODEX_SOURCE_HOME",
            Path("~/.codex").expanduser(),
            base_dir=env_base_dir,
        ),
        "codex_runtime_home": env_path(
            "CODEX_RUNTIME_HOME",
            data_dir / "codex-runtime",
            base_dir=app_home,
        ),
        "db_path": env_path(
            "DB_PATH",
            data_dir / "issue_bot.db",
            base_dir=app_home,
        ),
        "job_root": env_path(
            "JOB_ROOT",
            data_dir / "jobs",
            base_dir=app_home,
        ),
        "repo_root": env_path(
            "REPO_ROOT",
            data_dir / "repos",
            base_dir=app_home,
        ),
        "repo_lock_wait_seconds": env_int("REPO_LOCK_WAIT_SECONDS", 7200),
        "sync_script_path": os.getenv("SYNC_SCRIPT_PATH", "scripts/sync.sh").strip(),
        "merge_script_path": os.getenv("MERGE_SCRIPT_PATH", "scripts/merge.sh").strip(),
        "git_author_name": os.getenv("GIT_AUTHOR_NAME", "Codex Bot"),
        "git_author_email": os.getenv("GIT_AUTHOR_EMAIL", "codex-bot@example.com"),
        "pr_title_prefix": os.getenv("PR_TITLE_PREFIX", "[Codex]"),
        "submit_comment_after_pr": env_bool("SUBMIT_COMMENT_AFTER_PR", True),
        "submit_comment_body": os.getenv("SUBMIT_COMMENT_BODY", "/submit").strip() or "/submit",
        "default_base_branch": os.getenv("DEFAULT_BASE_BRANCH", "").strip(),
        "test_command": os.getenv("TEST_COMMAND", "").strip(),
        "feishu_webhook_url": os.getenv("FEISHU_WEBHOOK_URL", "").strip(),
        "feishu_secret": os.getenv("FEISHU_SECRET", "").strip(),
        "active_dir": env_path(
            "ACTIVE_DIR",
            data_dir / "active",
            base_dir=app_home,
        ),
        "state_file": env_path(
            "STATE_FILE",
            data_dir / "state.json",
            base_dir=app_home,
        ),
        "poll_enabled": env_bool("ENABLE_POLLING", True),
        "poll_interval_seconds": env_int("POLL_INTERVAL_SECONDS", 60),
        "dispatch_interval_seconds": env_int("DISPATCH_INTERVAL_SECONDS", 5),
        "issue_scan_limit": env_int("ISSUE_SCAN_LIMIT", 30),
        "comment_scan_limit": env_int("COMMENT_SCAN_LIMIT", 30),
        "fork_wait_timeout_seconds": env_int("FORK_WAIT_TIMEOUT_SECONDS", 300),
    }


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def shift_utc_timestamp(value: str | None, *, seconds: int) -> str | None:
    timestamp = parse_utc_timestamp(value)
    if timestamp is None:
        return None
    shifted = timestamp + timedelta(seconds=seconds)
    return shifted.strftime("%Y-%m-%dT%H:%M:%SZ")


def newer_utc_timestamp(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return left if left >= right else right


def short_text(text: str, limit: int = 1000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def tail_text(text: str, limit: int = 3000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "...\n" + text[-limit:]


def slugify(text: str, limit: int = 40) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    if not text:
        return "task"
    return text[:limit].strip("-") or "task"


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def copy_if_exists(source: Path, target: Path) -> None:
    if not source.is_file():
        return
    ensure_dir(target.parent)
    shutil.copy2(source, target)


def sync_codex_runtime_home(config: dict[str, Any]) -> Path:
    source_home = Path(config["codex_source_home"]).expanduser()
    runtime_home = ensure_dir(Path(config["codex_runtime_home"]).expanduser())

    for name in ["sessions", "log", "memories", "tmp", ".tmp", "shell_snapshots"]:
        ensure_dir(runtime_home / name)

    if not source_home.exists():
        return runtime_home

    for name in ["auth.json", "config.toml", "installation_id", "version.json"]:
        copy_if_exists(source_home / name, runtime_home / name)

    return runtime_home


def base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_app_jwt(config: dict[str, Any]) -> str:
    private_key_path = Path(config["github_private_key_path"])
    private_key = serialization.load_pem_private_key(
        private_key_path.read_bytes(),
        password=None,
    )
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {
        "iat": now - 60,
        "exp": now + 540,
        "iss": config["github_app_id"],
    }
    signing_input = (
        f"{base64url(json.dumps(header, separators=(',', ':')).encode('utf-8'))}."
        f"{base64url(json.dumps(payload, separators=(',', ':')).encode('utf-8'))}"
    )
    signature = private_key.sign(
        signing_input.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return f"{signing_input}.{base64url(signature)}"


def github_request(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    token: str | None = None,
    jwt_token: str | None = None,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> requests.Response:
    url = f"{config['github_api_url']}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "codex-issue-bot",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"
    if extra_headers:
        headers.update(extra_headers)
    response = requests.request(
        method,
        url,
        headers=headers,
        json=json_body,
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response


def get_installation_token(config: dict[str, Any], installation_id: str | None = None) -> str:
    install_id = installation_id or config["github_installation_id"]
    jwt_token = build_app_jwt(config)
    response = github_request(
        config,
        "POST",
        f"/app/installations/{install_id}/access_tokens",
        jwt_token=jwt_token,
        json_body={},
    )
    return response.json()["token"]


def get_repo_info(config: dict[str, Any], token: str, owner: str, repo: str) -> dict[str, Any]:
    return github_request(config, "GET", f"/repos/{owner}/{repo}", token=token).json()


def get_repo_info_optional(
    config: dict[str, Any], token: str, owner: str, repo: str
) -> dict[str, Any] | None:
    try:
        return get_repo_info(config, token, owner, repo)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def list_open_issues(
    config: dict[str, Any],
    token: str,
    owner: str,
    repo: str,
    *,
    since: str | None = None,
    etag: str | None = None,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    params = {
        "state": "open",
        "sort": "updated",
        "direction": "desc",
        "per_page": config["issue_scan_limit"],
    }
    if since:
        params["since"] = since

    first_page_headers = {"If-None-Match": etag} if etag else None
    first_response = github_request(
        config,
        "GET",
        f"/repos/{owner}/{repo}/issues",
        token=token,
        params={**params, "page": 1},
        extra_headers=first_page_headers,
    )
    if first_response.status_code == 304:
        return [], etag, True

    issues = list(first_response.json())
    if not since:
        return issues, first_response.headers.get("ETag"), False

    page = 2
    while True:
        response = github_request(
            config,
            "GET",
            f"/repos/{owner}/{repo}/issues",
            token=token,
            params={**params, "page": page},
        )
        batch = response.json()
        if not batch:
            break
        issues.extend(batch)
        if len(batch) < config["issue_scan_limit"]:
            break
        page += 1

    return issues, first_response.headers.get("ETag"), False


def list_issue_comments(
    config: dict[str, Any],
    token: str,
    owner: str,
    repo: str,
    issue_number: int,
    *,
    since: str | None = None,
    page: int = 1,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"per_page": config["comment_scan_limit"], "page": page}
    if since:
        params["since"] = since
    response = github_request(
        config,
        "GET",
        f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
        token=token,
        params=params,
    )
    return response.json()


def list_recent_issue_comments(
    config: dict[str, Any],
    token: str,
    owner: str,
    repo: str,
    issue_number: int,
    total_comments: int,
    *,
    since: str | None = None,
) -> list[dict[str, Any]]:
    per_page = max(1, config["comment_scan_limit"])
    if since:
        comments: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = list_issue_comments(
                config,
                token,
                owner,
                repo,
                issue_number,
                since=since,
                page=page,
            )
            if not batch:
                break
            comments.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return comments

    if total_comments <= per_page:
        return list_issue_comments(config, token, owner, repo, issue_number)

    last_page = max(1, (total_comments + per_page - 1) // per_page)
    pages: list[list[dict[str, Any]]] = []
    fetched = 0
    page = last_page
    while page >= 1 and fetched < per_page:
        batch = list_issue_comments(config, token, owner, repo, issue_number, page=page)
        if not batch:
            break
        pages.insert(0, batch)
        fetched += len(batch)
        page -= 1

    comments = [comment for batch in pages for comment in batch]
    if len(comments) <= per_page:
        return comments
    return comments[-per_page:]


def create_fork(config: dict[str, Any], token: str, owner: str, repo: str) -> None:
    github_request(
        config,
        "POST",
        f"/repos/{owner}/{repo}/forks",
        token=token,
        json_body={},
    )


def list_pull_requests(
    config: dict[str, Any],
    token: str,
    owner: str,
    repo: str,
    *,
    head: str,
    base: str,
    state: str = "open",
) -> list[dict[str, Any]]:
    response = github_request(
        config,
        "GET",
        f"/repos/{owner}/{repo}/pulls",
        token=token,
        params={"head": head, "base": base, "state": state, "per_page": 10},
    )
    return response.json()


def create_pull_request(
    config: dict[str, Any],
    token: str,
    owner: str,
    repo: str,
    *,
    title: str,
    body: str,
    head: str,
    base: str,
) -> dict[str, Any]:
    return github_request(
        config,
        "POST",
        f"/repos/{owner}/{repo}/pulls",
        token=token,
        json_body={
            "title": title,
            "body": body,
            "head": head,
            "base": base,
            "maintainer_can_modify": False,
        },
    ).json()


def comment_issue(
    config: dict[str, Any],
    token: str,
    owner: str,
    repo: str,
    issue_number: int,
    body: str,
) -> None:
    github_request(
        config,
        "POST",
        f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
        token=token,
        json_body={"body": body},
    )


def build_feishu_sign(secret: str, timestamp: str) -> str:
    message = f"{timestamp}\n{secret}"
    digest = hmac.new(message.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def notify_feishu(config: dict[str, Any], message: str) -> None:
    webhook = config["feishu_webhook_url"]
    if not webhook:
        return
    payload: dict[str, Any] = {
        "msg_type": "text",
        "content": {"text": short_text(message, 3800)},
    }
    if config["feishu_secret"]:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = build_feishu_sign(config["feishu_secret"], timestamp)
    response = requests.post(
        webhook,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    response.raise_for_status()


def run_command(
    command: list[str] | str,
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    shell: bool = False,
    stdin: Any | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        input=input_text,
        capture_output=True,
        timeout=timeout,
        shell=shell,
        stdin=stdin,
        check=False,
    )


def command_exists(command: str) -> bool:
    candidate = Path(command).expanduser()
    if candidate.is_absolute() or "/" in command:
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(command) is not None


def path_readable(path: str) -> bool:
    target = Path(path)
    return target.is_file() and os.access(target, os.R_OK)


def ensure_writable_path(target: Path, *, is_file: bool) -> None:
    if is_file:
        parent = ensure_dir(target.parent)
        probe = parent / f".write-test-{os.getpid()}"
    else:
        directory = ensure_dir(target)
        probe = directory / f".write-test-{os.getpid()}"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()


def collect_config_errors(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if not config["webhook_enabled"] and not config["poll_enabled"]:
        errors.append("ENABLE_WEBHOOK 和 ENABLE_POLLING 不能同时关闭。")

    if config["webhook_enabled"] and not config["webhook_secret"]:
        errors.append("ENABLE_WEBHOOK=true 时必须配置 GITHUB_WEBHOOK_SECRET。")

    if not config["allowed_repos"]:
        errors.append("ALLOWED_REPOS 不能为空。")

    for key in ["github_app_id", "github_installation_id"]:
        if not config.get(key):
            errors.append(f"{key.upper()} 不能为空。")

    if not config["github_private_key_path"]:
        errors.append("GITHUB_PRIVATE_KEY_PATH 不能为空。")
    elif not path_readable(config["github_private_key_path"]):
        errors.append("GITHUB_PRIVATE_KEY_PATH 指向的私钥文件不存在或不可读。")

    if config["github_clone_mode"] != "ssh":
        errors.append("当前只支持 GITHUB_CLONE_MODE=ssh。")

    if config["github_clone_mode"] == "ssh" and not config["github_clone_ssh_key_path"]:
        errors.append("GITHUB_CLONE_SSH_KEY_PATH 不能为空。")
    elif config["github_clone_mode"] == "ssh" and not path_readable(config["github_clone_ssh_key_path"]):
        errors.append("GITHUB_CLONE_SSH_KEY_PATH 指向的 SSH 私钥不存在或不可读。")

    if not config["codex_bin"]:
        errors.append("CODEX_BIN 不能为空。")
    elif not command_exists(config["codex_bin"]):
        errors.append("CODEX_BIN 不存在或不可执行。")

    if not config["codex_model"]:
        errors.append("CODEX_MODEL 不能为空。")

    if not config["trigger_comment"] and not config["trigger_label"] and not config["run_on_issue_opened"]:
        errors.append("至少需要保留一种触发方式：TRIGGER_COMMENT、TRIGGER_LABEL 或 RUN_ON_ISSUE_OPENED=true。")

    return errors


def validate_config(config: dict[str, Any]) -> None:
    errors = collect_config_errors(config)
    if errors:
        raise SystemExit("配置校验失败：\n- " + "\n- ".join(errors))


def run_doctor(config: dict[str, Any], env_file: Path) -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))

    check("env 文件", env_file.exists(), str(env_file.resolve(strict=False)))

    config_errors = collect_config_errors(config)
    if config_errors:
        check("配置基础校验", False, "；".join(config_errors))
    else:
        check("配置基础校验", True, "必填项和开关组合正常")

    private_key_path = Path(config["github_private_key_path"])
    check(
        "GitHub App 私钥",
        path_readable(config["github_private_key_path"]),
        str(private_key_path),
    )

    ssh_key_path = Path(config["github_clone_ssh_key_path"])
    check(
        "Git SSH 私钥",
        path_readable(config["github_clone_ssh_key_path"]),
        str(ssh_key_path),
    )

    codex_exists = command_exists(config["codex_bin"])
    check("Codex 可执行文件", codex_exists, config["codex_bin"])
    if codex_exists:
        try:
            codex_probe = run_command(
                [config["codex_bin"], "--version"],
                cwd=Path(config["app_home"]),
                timeout=30,
            )
            probe_output = tail_text(
                "\n".join(part for part in [codex_probe.stdout, codex_probe.stderr] if part),
                500,
            ) or config["codex_bin"]
            check("Codex CLI 可运行", codex_probe.returncode == 0, probe_output)
        except Exception as exc:
            check("Codex CLI 可运行", False, str(exc))
    else:
        check("Codex CLI 可运行", False, "skipped: executable missing")
    check("GitHub CLI", command_exists("gh"), "gh")
    check(
        "Gunicorn",
        importlib.util.find_spec("gunicorn") is not None or command_exists("gunicorn"),
        "gunicorn",
    )

    codex_source_home = Path(config["codex_source_home"])
    check(
        "CODEX_SOURCE_HOME",
        codex_source_home.exists(),
        str(codex_source_home),
    )

    try:
        ensure_writable_path(Path(config["db_path"]), is_file=True)
        ensure_writable_path(Path(config["job_root"]), is_file=False)
        ensure_writable_path(Path(config["repo_root"]), is_file=False)
        ensure_writable_path(Path(config["active_dir"]), is_file=False)
        ensure_writable_path(Path(config["state_file"]), is_file=True)
        check("目录写权限", True, "DB_PATH / JOB_ROOT / REPO_ROOT / ACTIVE_DIR / STATE_FILE 可写")
    except Exception as exc:
        check("目录写权限", False, str(exc))

    try:
        sync_codex_runtime_home(config)
        check("Codex 运行时目录", True, config["codex_runtime_home"])
    except Exception as exc:
        check("Codex 运行时目录", False, str(exc))

    try:
        init_db()
        check("SQLite", True, config["db_path"])
    except Exception as exc:
        check("SQLite", False, str(exc))

    try:
        build_app_jwt(config)
        check("GitHub App JWT", True, "私钥可解析")
    except Exception as exc:
        check("GitHub App JWT", False, str(exc))

    try:
        get_installation_token(config)
        check("GitHub Installation Token", True, "access token acquired")
    except Exception as exc:
        check("GitHub Installation Token", False, str(exc))

    if config["github_fork_installation_id"]:
        try:
            get_installation_token(config, config["github_fork_installation_id"])
            check("Fork Installation Token", True, "access token acquired")
        except Exception as exc:
            check("Fork Installation Token", False, str(exc))
    else:
        check("Fork Installation Token", True, "未配置，表示使用手工创建的 fork")

    print("Coding Bot Doctor")
    print(f"APP_HOME: {config['app_home']}")
    print(f"DATA_DIR: {config['data_dir']}")
    print(f"SECRETS_DIR: {config['secrets_dir']}")
    print(f"ALLOWED_REPOS: {', '.join(config['allowed_repos']) or '(empty)'}")
    print("")
    for name, ok, detail in results:
        prefix = "[OK]" if ok else "[FAIL]"
        print(f"{prefix} {name}: {detail}")

    failed = [item for item in results if not item[1]]
    print("")
    if failed:
        print(f"Doctor 完成：{len(failed)} 项失败。")
        return 1

    print("Doctor 完成：全部通过。")
    return 0


def db_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(
        str(Path(CONFIG["db_path"])),
        timeout=30,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_db() -> None:
    ensure_dir(Path(CONFIG["db_path"]).parent)
    with DB_LOCK:
        with db_connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    event_name TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS issues (
                    repo_full_name TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    issue_title TEXT NOT NULL,
                    issue_state TEXT NOT NULL,
                    active_job_id TEXT,
                    last_reason TEXT,
                    updated_at TEXT NOT NULL,
                    closed_at TEXT,
                    PRIMARY KEY (repo_full_name, issue_number)
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    repo_full_name TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    worker_pid INTEGER,
                    error_text TEXT,
                    result_summary TEXT,
                    job_dir TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status_repo_created
                ON jobs (status, repo_full_name, created_at);

                CREATE INDEX IF NOT EXISTS idx_jobs_repo_issue_created
                ON jobs (repo_full_name, issue_number, created_at);
                """
            )


def fetchone(query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    with DB_LOCK:
        with db_connect() as connection:
            return connection.execute(query, params).fetchone()


def fetchall(query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with DB_LOCK:
        with db_connect() as connection:
            return list(connection.execute(query, params).fetchall())


def execute(query: str, params: tuple[Any, ...] = ()) -> None:
    with DB_LOCK:
        with db_connect() as connection:
            connection.execute(query, params)


def record_delivery_once(delivery_id: str, event_name: str) -> bool:
    now = now_utc()
    with DB_LOCK:
        with db_connect() as connection:
            existing = connection.execute(
                "SELECT delivery_id FROM deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if existing:
                return False
            connection.execute(
                "INSERT INTO deliveries (delivery_id, event_name, received_at) VALUES (?, ?, ?)",
                (delivery_id, event_name, now),
            )
            connection.execute(
                """
                DELETE FROM deliveries
                WHERE delivery_id NOT IN (
                    SELECT delivery_id FROM deliveries ORDER BY received_at DESC LIMIT 5000
                )
                """
            )
            return True


def upsert_issue_record(
    repo_full_name: str,
    issue_number: int,
    issue_title: str,
    issue_state: str,
    *,
    active_job_id: str | None = None,
    last_reason: str | None = None,
) -> None:
    now = now_utc()
    closed_at = now if issue_state == "closed" else None
    with DB_LOCK:
        with db_connect() as connection:
            connection.execute(
                """
                INSERT INTO issues (
                    repo_full_name, issue_number, issue_title, issue_state,
                    active_job_id, last_reason, updated_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_full_name, issue_number) DO UPDATE SET
                    issue_title = excluded.issue_title,
                    issue_state = excluded.issue_state,
                    active_job_id = COALESCE(excluded.active_job_id, issues.active_job_id),
                    last_reason = COALESCE(excluded.last_reason, issues.last_reason),
                    updated_at = excluded.updated_at,
                    closed_at = excluded.closed_at
                """,
                (
                    repo_full_name,
                    issue_number,
                    issue_title,
                    issue_state,
                    active_job_id,
                    last_reason,
                    now,
                    closed_at,
                ),
            )


def clear_issue_active_job(repo_full_name: str, issue_number: int, job_id: str) -> None:
    execute(
        """
        UPDATE issues
        SET active_job_id = NULL, updated_at = ?
        WHERE repo_full_name = ? AND issue_number = ? AND active_job_id = ?
        """,
        (now_utc(), repo_full_name, issue_number, job_id),
    )


def get_existing_active_job(repo_full_name: str, issue_number: int) -> sqlite3.Row | None:
    statuses = tuple(ACTIVE_JOB_STATUSES)
    placeholders = ", ".join("?" for _ in statuses)
    return fetchone(
        f"""
        SELECT job_id, job_dir, status
        FROM jobs
        WHERE repo_full_name = ? AND issue_number = ? AND status IN ({placeholders})
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (repo_full_name, issue_number, *statuses),
    )


def issue_has_active_job(repo_full_name: str, issue_number: int) -> bool:
    return get_existing_active_job(repo_full_name, issue_number) is not None


def repo_has_running_job(repo_full_name: str) -> bool:
    row = fetchone(
        "SELECT job_id FROM jobs WHERE repo_full_name = ? AND status = 'running' LIMIT 1",
        (repo_full_name,),
    )
    return row is not None


def repo_has_active_job(repo_full_name: str) -> bool:
    statuses = tuple(ACTIVE_JOB_STATUSES)
    placeholders = ", ".join("?" for _ in statuses)
    row = fetchone(
        f"SELECT job_id FROM jobs WHERE repo_full_name = ? AND status IN ({placeholders}) LIMIT 1",
        (repo_full_name, *statuses),
    )
    return row is not None


def create_job(payload: dict[str, Any], reason: str) -> tuple[str, Path, bool]:
    repo_full_name = payload["repository"]["full_name"]
    issue = payload["issue"]
    issue_number = int(issue["number"])
    issue_title = issue.get("title") or f"Issue #{issue_number}"
    issue_state = issue.get("state") or "open"

    existing = get_existing_active_job(repo_full_name, issue_number)
    if existing:
        return str(existing["job_id"]), Path(str(existing["job_dir"])), False

    job_id = f"issue-{issue_number}-{int(time.time() * 1000)}"
    job_dir = ensure_dir(Path(CONFIG["job_root"]) / job_id)
    job_data = {
        "job_id": job_id,
        "queued_at": now_utc(),
        "reason": reason,
        "payload": payload,
    }
    (job_dir / "job.json").write_text(
        json.dumps(job_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    payload_json = json.dumps(payload, ensure_ascii=False)
    created_at = now_utc()
    with DB_LOCK:
        with db_connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, repo_full_name, issue_number, reason, payload_json,
                    status, created_at, job_dir
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    job_id,
                    repo_full_name,
                    issue_number,
                    reason,
                    payload_json,
                    created_at,
                    str(job_dir),
                ),
            )
            connection.execute(
                """
                INSERT INTO issues (
                    repo_full_name, issue_number, issue_title, issue_state,
                    active_job_id, last_reason, updated_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_full_name, issue_number) DO UPDATE SET
                    issue_title = excluded.issue_title,
                    issue_state = excluded.issue_state,
                    active_job_id = excluded.active_job_id,
                    last_reason = excluded.last_reason,
                    updated_at = excluded.updated_at,
                    closed_at = excluded.closed_at
                """,
                (
                    repo_full_name,
                    issue_number,
                    issue_title,
                    issue_state,
                    job_id,
                    reason,
                    created_at,
                    created_at if issue_state == "closed" else None,
                ),
            )
    return job_id, job_dir, True


def get_job(job_id: str) -> sqlite3.Row | None:
    return fetchone("SELECT * FROM jobs WHERE job_id = ?", (job_id,))


def mark_job_running(job_id: str, pid: int) -> None:
    execute(
        """
        UPDATE jobs
        SET status = 'running', started_at = ?, worker_pid = ?, finished_at = NULL, error_text = NULL
        WHERE job_id = ?
        """,
        (now_utc(), pid, job_id),
    )


def mark_job_finished(
    job_id: str,
    status: str,
    *,
    error_text: str | None = None,
    result_summary: str | None = None,
) -> None:
    execute(
        """
        UPDATE jobs
        SET status = ?, finished_at = ?, worker_pid = NULL, error_text = ?, result_summary = ?
        WHERE job_id = ?
        """,
        (status, now_utc(), error_text, result_summary, job_id),
    )


def requeue_job(job_id: str, error_text: str | None = None) -> None:
    execute(
        """
        UPDATE jobs
        SET status = 'queued', started_at = NULL, finished_at = NULL, worker_pid = NULL, error_text = ?
        WHERE job_id = ?
        """,
        (error_text, job_id),
    )


def repo_workspace_root(config: dict[str, Any], repo_full_name: str) -> Path:
    safe_name = repo_full_name.replace("/", "__")
    return ensure_dir(Path(config["repo_root"]) / safe_name)


def repo_checkout_dir(config: dict[str, Any], repo_full_name: str) -> Path:
    return repo_workspace_root(config, repo_full_name) / "repo"


def active_lock_path(config: dict[str, Any], repo_full_name: str, issue_number: int) -> Path:
    safe_name = repo_full_name.replace("/", "__")
    return ensure_dir(config["active_dir"]) / f"{safe_name}__{issue_number}.lock"


def repo_lock_path(config: dict[str, Any], repo_full_name: str) -> Path:
    safe_name = repo_full_name.replace("/", "__")
    return ensure_dir(config["active_dir"]) / f"{safe_name}__repo.lock"


def acquire_file_lock(target: Path) -> bool:
    try:
        fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(now_utc())
        return True
    except FileExistsError:
        return False


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


def default_state() -> dict[str, Any]:
    return {
        "processed_triggers": {},
        "poll_cache": {
            "repos": {},
            "issues": {},
        },
    }


def normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    processed = state.get("processed_triggers")
    if not isinstance(processed, dict):
        state["processed_triggers"] = {}

    poll_cache = state.get("poll_cache")
    if not isinstance(poll_cache, dict):
        poll_cache = {}
        state["poll_cache"] = poll_cache

    repos = poll_cache.get("repos")
    if not isinstance(repos, dict):
        poll_cache["repos"] = {}

    issues = poll_cache.get("issues")
    if not isinstance(issues, dict):
        poll_cache["issues"] = {}

    return state


def poll_cache_repo_state(state: dict[str, Any], repo_full_name: str) -> dict[str, Any]:
    normalized = normalize_state(state)
    poll_cache = normalized["poll_cache"]
    repos = poll_cache["repos"]
    repo_state = repos.get(repo_full_name)
    if not isinstance(repo_state, dict):
        repo_state = {}
        repos[repo_full_name] = repo_state
    return repo_state


def poll_cache_issue_key(repo_full_name: str, issue_number: int) -> str:
    return f"{repo_full_name}#{issue_number}"


def poll_cache_issue_state(state: dict[str, Any], repo_full_name: str, issue_number: int) -> dict[str, Any]:
    normalized = normalize_state(state)
    poll_cache = normalized["poll_cache"]
    issues = poll_cache["issues"]
    issue_key = poll_cache_issue_key(repo_full_name, issue_number)
    issue_state = issues.get(issue_key)
    if not isinstance(issue_state, dict):
        issue_state = {}
        issues[issue_key] = issue_state
    return issue_state


def state_set(target: dict[str, Any], key: str, value: Any) -> bool:
    current = target.get(key)
    if current == value:
        return False
    if value is None:
        if key not in target:
            return False
        target.pop(key, None)
        return True
    target[key] = value
    return True


def load_state() -> dict[str, Any]:
    path = Path(CONFIG["state_file"])
    ensure_dir(path.parent)
    with STATE_LOCK:
        if not path.exists():
            return default_state()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default_state()
    if not isinstance(data, dict):
        return default_state()
    return normalize_state(data)


def save_state(state: dict[str, Any]) -> None:
    path = Path(CONFIG["state_file"])
    ensure_dir(path.parent)
    normalized = normalize_state(state)
    processed = normalized["processed_triggers"]
    if len(processed) > 2000:
        trimmed = sorted(processed.items(), key=lambda item: item[1])[-1000:]
        normalized["processed_triggers"] = dict(trimmed)
    tmp_path = path.with_suffix(".tmp")
    payload = json.dumps(normalized, ensure_ascii=False, indent=2)
    with STATE_LOCK:
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)


def remove_issue_from_state(repo_full_name: str, issue_number: int) -> None:
    prefix = f"{repo_full_name}#{issue_number}:"
    state = load_state()
    processed = state.setdefault("processed_triggers", {})
    keys = [key for key in processed if key.startswith(prefix)]
    issue_cache_key = poll_cache_issue_key(repo_full_name, issue_number)
    issue_cache = state.setdefault("poll_cache", {}).setdefault("issues", {})
    removed = False
    if not keys:
        if issue_cache.pop(issue_cache_key, None) is None:
            return
        save_state(state)
        return
    for key in keys:
        processed.pop(key, None)
        removed = True
    if issue_cache.pop(issue_cache_key, None) is not None:
        removed = True
    if not removed:
        return
    save_state(state)


def trigger_key(repo_full_name: str, issue_number: int, kind: str, value: str) -> str:
    return f"{repo_full_name}#{issue_number}:{kind}:{value}"


def build_payload(
    repo_full_name: str,
    issue: dict[str, Any],
    *,
    action: str,
    label_name: str | None = None,
    comment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "repository": {"full_name": repo_full_name},
        "issue": issue,
    }
    if label_name is not None:
        payload["label"] = {"name": label_name}
    if comment is not None:
        payload["comment"] = comment
    return payload


def comment_marker(comment: dict[str, Any]) -> tuple[str, int]:
    created_at = str(comment.get("created_at") or "")
    try:
        comment_id = int(comment.get("id") or 0)
    except (TypeError, ValueError):
        comment_id = 0
    return created_at, comment_id


def comment_is_newer_than(comment: dict[str, Any], created_at: str | None, comment_id: int) -> bool:
    current_created_at, current_comment_id = comment_marker(comment)
    if not created_at:
        return True
    if current_created_at > created_at:
        return True
    if current_created_at < created_at:
        return False
    return current_comment_id > comment_id


def build_git_ssh_env(config: dict[str, Any], base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = (base_env or os.environ).copy()
    env["GIT_SSH_COMMAND"] = (
        f"ssh -i {config['github_clone_ssh_key_path']} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
    )
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


def ensure_repo_checkout(
    config: dict[str, Any],
    repo_full_name: str,
    default_branch: str,
    branch_name: str,
) -> tuple[Path, Path]:
    upstream_owner, repo = repo_full_name.split("/", 1)
    fork_owner = config["github_fork_owner"]
    workspace_root = repo_workspace_root(config, repo_full_name)
    repo_dir = repo_checkout_dir(config, repo_full_name)
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
        git_checkout_branch(repo_dir, branch_name, f"origin/{default_branch}", label="branch")
        return workspace_root, repo_dir

    # Fall back to a generic upstream-based checkout for repos that do not ship
    # custom sync scripts.
    git_checkout_branch(repo_dir, default_branch, f"upstream/{default_branch}", label="base")
    git_checkout_branch(repo_dir, branch_name, f"upstream/{default_branch}", label="branch")

    return workspace_root, repo_dir


def extract_pull_request_url(text: str) -> str | None:
    match = re.search(r"https://github\.com/[^\s]+/pull/\d+", text)
    return match.group(0) if match else None


def publish_pull_request_via_merge_script(
    config: dict[str, Any],
    token: str,
    work_dir: Path,
    upstream_owner: str,
    repo: str,
    base_branch: str,
    pr_title: str,
    pr_body: str,
) -> dict[str, Any]:
    branch_result = run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=work_dir,
        timeout=30,
    )
    current_branch = (branch_result.stdout or "").strip() if branch_result.returncode == 0 else ""
    if not current_branch:
        raise RuntimeError("could not determine current branch for PR publish")
    head_ref = f"{config['github_fork_owner']}:{current_branch}"

    merge_script = work_dir / config["merge_script_path"]
    if not merge_script.is_file():
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
            return {"html_url": existing[0]["html_url"], "method": "git+api"}
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
        return {"html_url": pr["html_url"], "method": "git+api"}

    if not command_exists("gh"):
        raise RuntimeError("GitHub CLI `gh` is missing; install it before running merge.sh")

    env = build_git_ssh_env(config)
    env["GH_TOKEN"] = token
    env["AUTO_PUSH"] = "true"
    env["INTERACTIVE"] = "false"
    env["AUTO_FILL_PR"] = "false"
    env["PREFER_GH_AUTH_LOGIN"] = "false"
    env["PR_TITLE"] = pr_title
    env["PR_BODY"] = pr_body

    merge_result = run_command(
        ["bash", str(merge_script), base_branch],
        cwd=work_dir,
        env=env,
        timeout=300,
    )
    combined_output = "\n".join(part for part in [merge_result.stdout, merge_result.stderr] if part)

    if merge_result.returncode != 0:
        if (
            "createPullRequest" in combined_output
            or "Fork collab" in combined_output
            or "Resource not accessible by integration" in combined_output
        ):
            existing = list_pull_requests(
                config,
                token,
                upstream_owner,
                repo,
                head=head_ref,
                base=base_branch,
            )
            if existing:
                return {"html_url": existing[0]["html_url"]}
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
            return {"html_url": pr["html_url"], "method": "merge.sh+api"}
        raise RuntimeError(f"merge.sh failed\n{tail_text(combined_output, 3000)}")

    pr_url = extract_pull_request_url(combined_output)
    if not pr_url:
        view_result = run_command(
            ["gh", "pr", "view", "--json", "url", "--jq", ".url"],
            cwd=work_dir,
            env=env,
            timeout=60,
        )
        if view_result.returncode == 0:
            pr_url = (view_result.stdout or "").strip() or None
    if not pr_url:
        raise RuntimeError("merge.sh completed but PR URL could not be determined")
    return {"html_url": pr_url, "method": "merge.sh"}


def build_prompt(repo_full_name: str, issue: dict[str, Any], repo_path: str, test_command: str) -> str:
    issue_body = issue.get("body") or "(no issue body)"
    lines = [
        f"你正在处理 GitHub Issue #{issue['number']}。",
        f"仓库：{repo_full_name}",
        f"本地仓库路径：{repo_path}",
        f"Issue 标题：{issue['title']}",
        "",
        "目标：",
        "- 只解决当前这个 issue。",
        "- 只在当前仓库内工作。",
        "- 采用最小化修改方案，不做无关重构。",
        "",
        "硬性限制：",
        "- 不要执行 git commit、git push、创建 PR、调用 merge.sh、调用 sync.sh。",
        "- 不要修改 CI、部署配置、发布脚本、基础设施配置，除非 issue 明确要求且不改就无法解决。",
        "- 不要新增无关文档、示例文件、演示代码。",
        "- 不要修改其他仓库或访问当前仓库之外的路径。",
        "",
        "说明：",
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
    if test_command:
        lines.append(f"5. 如果配置了测试命令，执行：{test_command}")
    else:
        lines.append("5. 当前没有配置自动测试命令，至少做与你改动直接相关的最小自检。")
    lines.extend(
        [
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


def parse_codex_result(text: str) -> dict[str, str]:
    raw = (text or "").strip()
    if not raw:
        raise RuntimeError("codex returned empty final response")
    match = re.search(r"(?mi)^\s*result:\s*(succeeded|no_change|needs_human)\s*$", raw)
    if not match:
        raise RuntimeError("codex final response missing `result:` line")
    return {"status": match.group(1), "text": raw}


def run_codex(
    config: dict[str, Any],
    work_dir: Path,
    prompt: str,
    job_dir: Path,
) -> dict[str, str]:
    sync_codex_runtime_home(config)
    last_message_file = job_dir / "codex.last_message.txt"
    env = os.environ.copy()
    env["CODEX_HOME"] = config["codex_runtime_home"]

    command = [
        config["codex_bin"],
        "exec",
        "--cd",
        str(work_dir),
        "--model",
        config["codex_model"],
        "--color",
        "never",
        "--output-last-message",
        str(last_message_file),
        "--dangerously-bypass-approvals-and-sandbox",
        "-",
    ]
    result = run_command(
        command,
        cwd=work_dir,
        env=env,
        timeout=config["codex_timeout"] + 120,
        input_text=prompt,
    )
    (job_dir / "codex.stdout.log").write_text(result.stdout or "", encoding="utf-8")
    (job_dir / "codex.stderr.log").write_text(result.stderr or "", encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            "codex execution failed\n"
            f"{tail_text(result.stderr or result.stdout, 4000)}"
        )

    if not last_message_file.is_file():
        raise RuntimeError("codex execution completed but final message file is missing")

    return parse_codex_result(last_message_file.read_text(encoding="utf-8"))


def validate_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def repo_allowed(config: dict[str, Any], repo_full_name: str) -> bool:
    allowed = config["allowed_repos"]
    return not allowed or repo_full_name in allowed


def queue_payload(payload: dict[str, Any], reason: str) -> str:
    job_id, _, created = create_job(payload, reason)
    RUNTIME["last_queued_job_id"] = job_id
    if created:
        print(
            f"queued job {job_id} for "
            f"{payload['repository']['full_name']}#{payload['issue']['number']} ({reason})"
        )
    else:
        print(
            f"deduplicated to existing job {job_id} for "
            f"{payload['repository']['full_name']}#{payload['issue']['number']}"
        )
    dispatch_queued_jobs()
    return job_id


def pid_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recover_inflight_jobs(*, source: str = "service startup") -> None:
    running_jobs = fetchall(
        "SELECT job_id, worker_pid, repo_full_name, issue_number FROM jobs WHERE status = 'running'"
    )
    for row in running_jobs:
        if pid_is_alive(row["worker_pid"]):
            continue
        repo_full_name = str(row["repo_full_name"])
        issue_number = int(row["issue_number"])
        release_active_lock(CONFIG, repo_full_name, issue_number)
        release_repo_lock(CONFIG, repo_full_name)
        requeue_job(str(row["job_id"]), f"worker process missing; re-queued on {source}")


def spawn_worker(job_id: str, job_dir: Path) -> int:
    log_file = job_dir / "worker.log"
    with log_file.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--env-file",
                CONFIG["env_file"],
                "run-job",
                job_id,
            ],
            stdout=handle,
            stderr=handle,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return process.pid


def dispatch_queued_jobs() -> None:
    queued_jobs = fetchall(
        "SELECT job_id, repo_full_name, job_dir FROM jobs WHERE status = 'queued' ORDER BY created_at ASC"
    )
    repo_started: set[str] = set()
    for row in queued_jobs:
        repo_full_name = str(row["repo_full_name"])
        if repo_full_name in repo_started:
            continue
        if repo_has_running_job(repo_full_name):
            continue
        if repo_lock_path(CONFIG, repo_full_name).exists():
            continue
        job_id = str(row["job_id"])
        job_dir = Path(str(row["job_dir"]))
        pid = spawn_worker(job_id, job_dir)
        mark_job_running(job_id, pid)
        RUNTIME["last_dispatched_job_id"] = job_id
        repo_started.add(repo_full_name)


def dispatch_loop() -> None:
    interval = max(2, CONFIG["dispatch_interval_seconds"])
    while True:
        try:
            recover_inflight_jobs(source="dispatch loop")
            dispatch_queued_jobs()
        except Exception as exc:
            print(f"dispatch error: {exc}")
        time.sleep(interval)


def start_dispatch_thread() -> None:
    global DISPATCH_THREAD
    if DISPATCH_THREAD and DISPATCH_THREAD.is_alive():
        return
    DISPATCH_THREAD = threading.Thread(target=dispatch_loop, name="job-dispatcher", daemon=True)
    DISPATCH_THREAD.start()


def cleanup_closed_issue_if_finished(repo_full_name: str, issue_number: int) -> None:
    issue_row = fetchone(
        "SELECT issue_state FROM issues WHERE repo_full_name = ? AND issue_number = ?",
        (repo_full_name, issue_number),
    )
    if not issue_row or issue_row["issue_state"] != "closed":
        return
    active = fetchone(
        "SELECT job_id FROM jobs WHERE repo_full_name = ? AND issue_number = ? AND status IN ('queued', 'running') LIMIT 1",
        (repo_full_name, issue_number),
    )
    if active:
        return
    execute(
        "DELETE FROM jobs WHERE repo_full_name = ? AND issue_number = ?",
        (repo_full_name, issue_number),
    )
    execute(
        "DELETE FROM issues WHERE repo_full_name = ? AND issue_number = ?",
        (repo_full_name, issue_number),
    )
    remove_issue_from_state(repo_full_name, issue_number)


def handle_issue_closed(repo_full_name: str, issue: dict[str, Any]) -> None:
    issue_number = int(issue["number"])
    upsert_issue_record(
        repo_full_name,
        issue_number,
        issue.get("title") or f"Issue #{issue_number}",
        "closed",
    )
    execute(
        """
        UPDATE jobs
        SET status = 'cancelled', finished_at = ?, error_text = 'issue closed before execution'
        WHERE repo_full_name = ? AND issue_number = ? AND status = 'queued'
        """,
        (now_utc(), repo_full_name, issue_number),
    )
    cleanup_closed_issue_if_finished(repo_full_name, issue_number)


def process_job(job_id: str) -> None:
    row = get_job(job_id)
    if row is None:
        raise RuntimeError(f"job not found: {job_id}")
    if row["status"] in TERMINAL_JOB_STATUSES:
        return

    payload = json.loads(str(row["payload_json"]))
    repo_full_name = str(row["repo_full_name"])
    owner, repo = repo_full_name.split("/", 1)
    issue = payload["issue"]
    issue_number = int(row["issue_number"])
    issue_title = issue.get("title") or f"Issue #{issue_number}"
    job_dir = Path(str(row["job_dir"]))
    repo_lock_acquired = False
    token = ""
    final_status = "failed"
    final_summary = ""
    final_error: str | None = None
    try:
        if not acquire_active_lock(CONFIG, repo_full_name, issue_number):
            raise RuntimeError(f"active job already exists for {repo_full_name}#{issue_number}")

        repo_lock_acquired = acquire_repo_lock(CONFIG, repo_full_name)
        if not repo_lock_acquired:
            raise RuntimeError(f"repo lock wait timed out for {repo_full_name}")

        token = get_installation_token(CONFIG)
        repo_info = get_repo_info(CONFIG, token, owner, repo)
        default_branch = CONFIG["default_base_branch"] or repo_info["default_branch"]
        branch_name = (
            f"codex/issue-{issue_number}-"
            f"{slugify(issue.get('title', 'task'))}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )

        comment_issue(
            CONFIG,
            token,
            owner,
            repo,
            issue_number,
            textwrap.dedent(
                f"""
                Codex 已开始处理此任务。

                - Job: `{job_id}`
                - Trigger: `{row['reason']}`
                - Model: `{CONFIG['codex_model']}`
                """
            ).strip(),
        )

        _, work_dir = ensure_repo_checkout(CONFIG, repo_full_name, default_branch, branch_name)

        run_command(["git", "config", "user.name", CONFIG["git_author_name"]], cwd=work_dir, timeout=30)
        run_command(["git", "config", "user.email", CONFIG["git_author_email"]], cwd=work_dir, timeout=30)

        prompt = build_prompt(repo_full_name, issue, str(work_dir), CONFIG["test_command"])
        codex_result = run_codex(CONFIG, work_dir, prompt, job_dir)
        final_summary = codex_result["text"]

        if codex_result["status"] == "needs_human":
            final_status = "needs_human"
            comment_issue(
                CONFIG,
                token,
                owner,
                repo,
                issue_number,
                textwrap.dedent(
                    f"""
                    Codex 需要人工介入，未创建 PR。

                    - Job: `{job_id}`
                    - Result: `needs_human`
                    """
                ).strip(),
            )
            notify_feishu(
                CONFIG,
                f"Codex 需要人工介入\n仓库: {repo_full_name}\nIssue: #{issue_number} {issue_title}",
            )
            return

        test_result_text = "未配置测试命令。"
        if CONFIG["test_command"]:
            test_result = run_command(
                CONFIG["test_command"],
                cwd=work_dir,
                timeout=900,
                shell=True,
            )
            (job_dir / "test.stdout.log").write_text(test_result.stdout or "", encoding="utf-8")
            (job_dir / "test.stderr.log").write_text(test_result.stderr or "", encoding="utf-8")
            test_result_text = tail_text(
                "\n".join(part for part in [test_result.stdout, test_result.stderr] if part),
                2000,
            ) or "(no test output)"
            if test_result.returncode != 0:
                raise RuntimeError(f"test command failed\n{test_result_text}")

        status_result = run_command(["git", "status", "--porcelain"], cwd=work_dir, timeout=30)
        if status_result.returncode != 0:
            raise RuntimeError(f"git status failed\n{status_result.stderr}")

        ahead_result = run_command(
            ["git", "rev-list", "--count", f"origin/{default_branch}..HEAD"],
            cwd=work_dir,
            timeout=30,
        )
        if ahead_result.returncode != 0:
            raise RuntimeError(f"git rev-list failed\n{ahead_result.stderr}")
        ahead_count = int((ahead_result.stdout or "0").strip() or "0")

        if status_result.stdout.strip():
            add_result = run_command(["git", "add", "-A"], cwd=work_dir, timeout=60)
            if add_result.returncode != 0:
                raise RuntimeError(f"git add failed\n{add_result.stderr}")

            commit_result = run_command(
                ["git", "commit", "-m", f"fix: resolve issue #{issue_number}"],
                cwd=work_dir,
                timeout=120,
            )
            if commit_result.returncode != 0:
                raise RuntimeError(f"git commit failed\n{commit_result.stderr}")
            ahead_count += 1
        elif ahead_count == 0:
            final_status = "no_change"
            message = "Codex 执行完成，但未检测到代码改动，因此没有创建提交或 PR。"
            comment_issue(CONFIG, token, owner, repo, issue_number, message)
            notify_feishu(CONFIG, f"{repo_full_name}#{issue_number} 已执行，但没有产生代码改动。")
            return

        commit_message_result = run_command(
            ["git", "log", "-1", "--pretty=%B"],
            cwd=work_dir,
            timeout=30,
        )
        if commit_message_result.returncode != 0:
            raise RuntimeError(f"git log failed\n{commit_message_result.stderr}")

        pr_title = f"{CONFIG['pr_title_prefix']} resolve #{issue_number}: {short_text(issue_title, 72)}"
        pr_body = textwrap.dedent(
            f"""
            Closes #{issue_number}

            Automated by Codex.

            Trigger: `{row['reason']}`

            Test result:
            {test_result_text if CONFIG['test_command'] else '未配置测试命令。'}
            """
        ).strip()

        pr = publish_pull_request_via_merge_script(
            CONFIG,
            token,
            work_dir,
            owner,
            repo,
            default_branch,
            pr_title,
            pr_body,
        )
        final_status = "succeeded"
        publish_method = pr.get("method", "merge.sh")

        comment_issue(
            CONFIG,
            token,
            owner,
            repo,
            issue_number,
            textwrap.dedent(
                f"""
                Codex 已完成处理并创建 PR。

                - Branch: `{branch_name}`
                - PR: {pr['html_url']}
                - Publish: `{publish_method}`
                - Test: {"passed" if CONFIG["test_command"] else "not configured"}
                """
            ).strip(),
        )
        if CONFIG["submit_comment_after_pr"]:
            comment_issue(
                CONFIG,
                token,
                owner,
                repo,
                issue_number,
                CONFIG["submit_comment_body"],
            )
        notify_feishu(
            CONFIG,
            textwrap.dedent(
                f"""
                Codex 任务成功
                仓库: {repo_full_name}
                Issue: #{issue_number} {issue_title}
                PR: {pr['html_url']}
                Publish: {publish_method}
                Trigger: {row['reason']}
                """
            ).strip(),
        )
    except Exception as exc:
        final_status = "failed"
        failure_message = short_text(str(exc), 3500)
        final_error = failure_message
        print(f"[{job_id}] failed: {failure_message}")
        if token:
            try:
                comment_issue(
                    CONFIG,
                    token,
                    owner,
                    repo,
                    issue_number,
                    textwrap.dedent(
                        f"""
                        Codex 执行失败。

                        - Job: `{job_id}`
                        - Error: `{failure_message}`
                        """
                    ).strip(),
                )
            except Exception as comment_exc:
                print(f"comment failed: {comment_exc}")
        try:
            notify_feishu(
                CONFIG,
                textwrap.dedent(
                    f"""
                    Codex 任务失败
                    仓库: {repo_full_name}
                    Issue: #{issue_number} {issue_title}
                    Error: {failure_message}
                    """
                ).strip(),
            )
        except Exception as notify_exc:
            print(f"feishu notify failed: {notify_exc}")
        raise
    finally:
        mark_job_finished(
            job_id,
            final_status,
            error_text=final_error,
            result_summary=final_summary or None,
        )
        clear_issue_active_job(repo_full_name, issue_number, job_id)
        if repo_lock_acquired:
            release_repo_lock(CONFIG, repo_full_name)
        release_active_lock(CONFIG, repo_full_name, issue_number)
        cleanup_closed_issue_if_finished(repo_full_name, issue_number)


def webhook_decision(payload: dict[str, Any], event_name: str) -> tuple[bool, str]:
    action = payload.get("action", "")
    if event_name == "issues":
        if action == "opened" and CONFIG["run_on_issue_opened"]:
            return True, "issues.opened"
        if action == "labeled":
            label = payload.get("label", {}).get("name", "")
            if label == CONFIG["trigger_label"]:
                return True, f"issues.labeled:{label}"
    if event_name == "issue_comment" and action == "created":
        body = (payload.get("comment", {}).get("body") or "").strip()
        if body == CONFIG["trigger_comment"]:
            return True, f"issue_comment.created:{body}"
    return False, f"ignored:{event_name}.{action}"


def detect_poll_trigger(
    config: dict[str, Any],
    token: str,
    repo_full_name: str,
    issue: dict[str, Any],
    state: dict[str, Any],
) -> tuple[tuple[dict[str, Any], str, str] | None, bool, bool]:
    issue_number = int(issue["number"])
    processed = state.setdefault("processed_triggers", {})
    if active_lock_path(config, repo_full_name, issue_number).exists():
        return None, False, True
    if issue_has_active_job(repo_full_name, issue_number):
        return None, False, True

    owner, repo = repo_full_name.split("/", 1)
    issue_state = poll_cache_issue_state(state, repo_full_name, issue_number)
    issue_updated_at = str(issue.get("updated_at") or "")
    comments_total = int(issue.get("comments", 0) or 0)
    state_changed = False

    cached_comment_count = int(issue_state.get("last_comments_count") or 0)
    last_comment_created_at = str(issue_state.get("last_comment_created_at") or "")
    last_comment_id = int(issue_state.get("last_comment_id") or 0)
    needs_comment_scan = comments_total > 0 and (
        "last_comments_count" not in issue_state or comments_total > cached_comment_count
    )

    if needs_comment_scan:
        comments_since = shift_utc_timestamp(
            str(issue_state.get("last_comment_updated_at") or "") or None,
            seconds=-1,
        )
        comments = list_recent_issue_comments(
            config,
            token,
            owner,
            repo,
            issue_number,
            comments_total,
            since=comments_since,
        )
        if comments_total > cached_comment_count and not comments:
            return None, state_changed, True

        newest_comment_created_at = last_comment_created_at
        newest_comment_id = last_comment_id
        newest_comment_updated_at = str(issue_state.get("last_comment_updated_at") or "")
        for comment in comments:
            comment_updated_at = str(comment.get("updated_at") or "")
            newest_comment_updated_at = newer_utc_timestamp(newest_comment_updated_at, comment_updated_at) or ""
            if comment_is_newer_than(comment, newest_comment_created_at, newest_comment_id):
                newest_comment_created_at, newest_comment_id = comment_marker(comment)

        state_changed |= state_set(issue_state, "last_issue_updated_at", issue_updated_at)
        state_changed |= state_set(issue_state, "last_comments_count", comments_total)
        if newest_comment_updated_at:
            state_changed |= state_set(issue_state, "last_comment_updated_at", newest_comment_updated_at)
        if newest_comment_created_at:
            state_changed |= state_set(issue_state, "last_comment_created_at", newest_comment_created_at)
        if newest_comment_id:
            state_changed |= state_set(issue_state, "last_comment_id", newest_comment_id)

        for comment in reversed(comments):
            if not comment_is_newer_than(comment, last_comment_created_at, last_comment_id):
                continue
            body = (comment.get("body") or "").strip()
            if body != config["trigger_comment"]:
                continue
            key = trigger_key(repo_full_name, issue_number, "comment", str(comment["id"]))
            if key in processed:
                continue
            payload = build_payload(
                repo_full_name,
                issue,
                action="created",
                comment=comment,
            )
            return (payload, f"poll.issue_comment:{comment['id']}", key), state_changed, False
    else:
        state_changed |= state_set(issue_state, "last_issue_updated_at", issue_updated_at)
        state_changed |= state_set(issue_state, "last_comments_count", comments_total)

    label_names = {label.get("name", "") for label in issue.get("labels", [])}
    if config["trigger_label"] in label_names:
        key = trigger_key(repo_full_name, issue_number, "label", config["trigger_label"])
        if key not in processed:
            payload = build_payload(
                repo_full_name,
                issue,
                action="labeled",
                label_name=config["trigger_label"],
            )
            return (payload, f"poll.issues_labeled:{config['trigger_label']}", key), state_changed, False

    if config["run_on_issue_opened"]:
        key = trigger_key(repo_full_name, issue_number, "opened", "issue")
        if key not in processed:
            payload = build_payload(repo_full_name, issue, action="opened")
            return (payload, "poll.issues_opened", key), state_changed, False

    return None, state_changed, False


def poll_once() -> None:
    if not CONFIG["poll_enabled"] or not CONFIG["allowed_repos"]:
        return

    RUNTIME["last_poll_started_at"] = now_utc()
    RUNTIME["last_poll_error"] = None
    state = load_state()
    state_changed = False
    token = get_installation_token(CONFIG)

    for repo_full_name in CONFIG["allowed_repos"]:
        owner, repo = repo_full_name.split("/", 1)
        repo_state = poll_cache_repo_state(state, repo_full_name)
        repo_has_pending = repo_has_active_job(repo_full_name) or repo_lock_path(CONFIG, repo_full_name).exists()
        if repo_has_pending:
            state_changed |= state_set(repo_state, "force_full_scan", True)

        use_incremental = not bool(repo_state.get("force_full_scan"))
        issues_since = None
        issues_etag = None
        issues_etag_key = ""
        if use_incremental:
            issues_since = shift_utc_timestamp(str(repo_state.get("last_issue_updated_at") or "") or None, seconds=-1)
            issues_etag_key = issues_since or ""
            if str(repo_state.get("issues_etag_key") or "") == issues_etag_key:
                issues_etag = str(repo_state.get("issues_etag") or "") or None

        issues, latest_etag, not_modified = list_open_issues(
            CONFIG,
            token,
            owner,
            repo,
            since=issues_since,
            etag=issues_etag,
        )
        if not_modified:
            continue

        if use_incremental:
            state_changed |= state_set(repo_state, "issues_etag_key", issues_etag_key)
            state_changed |= state_set(repo_state, "issues_etag", latest_etag)

        repo_latest_updated_at = str(repo_state.get("last_issue_updated_at") or "")
        repo_needs_full_scan = False
        for issue in issues:
            if issue.get("pull_request"):
                continue
            decision, issue_state_changed, issue_requests_rescan = detect_poll_trigger(
                CONFIG,
                token,
                repo_full_name,
                issue,
                state,
            )
            state_changed |= issue_state_changed
            if issue_requests_rescan:
                repo_needs_full_scan = True
            else:
                repo_latest_updated_at = newer_utc_timestamp(
                    repo_latest_updated_at,
                    str(issue.get("updated_at") or ""),
                ) or repo_latest_updated_at
            if not decision:
                continue
            payload, reason, key = decision
            queue_payload(payload, reason)
            state.setdefault("processed_triggers", {})[key] = now_utc()
            state_changed = True

        if repo_needs_full_scan or repo_has_active_job(repo_full_name):
            state_changed |= state_set(repo_state, "force_full_scan", True)
            continue

        state_changed |= state_set(repo_state, "force_full_scan", False)
        if repo_latest_updated_at:
            state_changed |= state_set(repo_state, "last_issue_updated_at", repo_latest_updated_at)
        if not use_incremental:
            state_changed |= state_set(repo_state, "issues_etag_key", None)
            state_changed |= state_set(repo_state, "issues_etag", None)

    if state_changed:
        save_state(state)
    RUNTIME["last_poll_completed_at"] = now_utc()


def poll_loop() -> None:
    interval = max(15, CONFIG["poll_interval_seconds"])
    print(f"polling enabled: every {interval}s for {CONFIG['allowed_repos']}")
    while True:
        try:
            poll_once()
        except Exception as exc:
            RUNTIME["last_poll_error"] = short_text(str(exc), 1200)
            print(f"polling error: {exc}")
        time.sleep(interval)


def start_polling_thread() -> None:
    global POLLING_THREAD
    if not CONFIG["poll_enabled"]:
        print("polling disabled")
        return
    if POLLING_THREAD and POLLING_THREAD.is_alive():
        return
    POLLING_THREAD = threading.Thread(target=poll_loop, name="github-poller", daemon=True)
    POLLING_THREAD.start()


def queue_stats() -> dict[str, int]:
    rows = fetchall("SELECT status, COUNT(*) AS total FROM jobs GROUP BY status")
    stats = {str(row["status"]): int(row["total"]) for row in rows}
    return {
        "queued": stats.get("queued", 0),
        "running": stats.get("running", 0),
        "succeeded": stats.get("succeeded", 0),
        "failed": stats.get("failed", 0),
        "no_change": stats.get("no_change", 0),
        "needs_human": stats.get("needs_human", 0),
        "cancelled": stats.get("cancelled", 0),
    }


@APP.get("/")
def root() -> Any:
    return jsonify(
        {
            "service": "codex-issue-bot",
            "status": "ok",
            "time": now_utc(),
            "endpoint": "/github/webhook",
            "poll_enabled": CONFIG.get("poll_enabled"),
        }
    )


@APP.get("/health")
def health() -> Any:
    return jsonify(
        {
            "status": "ok",
            "time": now_utc(),
            "app_home": CONFIG.get("app_home"),
            "data_dir": CONFIG.get("data_dir"),
            "webhook_enabled": CONFIG.get("webhook_enabled"),
            "allowed_repos": CONFIG.get("allowed_repos"),
            "run_on_issue_opened": CONFIG.get("run_on_issue_opened"),
            "trigger_label": CONFIG.get("trigger_label"),
            "trigger_comment": CONFIG.get("trigger_comment"),
            "poll_enabled": CONFIG.get("poll_enabled"),
            "poll_interval_seconds": CONFIG.get("poll_interval_seconds"),
            "dispatch_interval_seconds": CONFIG.get("dispatch_interval_seconds"),
            "submit_comment_after_pr": CONFIG.get("submit_comment_after_pr"),
            "submit_comment_body": CONFIG.get("submit_comment_body"),
            "last_poll_started_at": RUNTIME.get("last_poll_started_at"),
            "last_poll_completed_at": RUNTIME.get("last_poll_completed_at"),
            "last_poll_error": RUNTIME.get("last_poll_error"),
            "last_queued_job_id": RUNTIME.get("last_queued_job_id"),
            "last_dispatched_job_id": RUNTIME.get("last_dispatched_job_id"),
            "queue": queue_stats(),
        }
    )


@APP.post("/github/webhook")
def github_webhook() -> Any:
    if not CONFIG.get("webhook_enabled", True):
        return jsonify({"ok": False, "error": "webhook disabled"}), 404

    raw_body = request.get_data()
    signature = request.headers.get("X-Hub-Signature-256")
    if not validate_signature(CONFIG["webhook_secret"], raw_body, signature):
        return jsonify({"ok": False, "error": "invalid signature"}), 401

    event_name = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    if delivery_id and not record_delivery_once(delivery_id, event_name):
        return jsonify({"ok": True, "ignored": "duplicate delivery", "delivery_id": delivery_id}), 200

    payload = request.get_json(silent=True) or {}
    if event_name == "ping":
        return jsonify({"ok": True, "event": "ping"}), 200

    repo_full_name = payload.get("repository", {}).get("full_name", "")
    issue = payload.get("issue") or {}
    issue_number = issue.get("number")
    if not repo_full_name or issue_number is None:
        return jsonify({"ok": True, "ignored": "missing repository or issue"}), 200
    if not repo_allowed(CONFIG, repo_full_name):
        return jsonify({"ok": True, "ignored": "repo not allowed", "repo": repo_full_name}), 200

    if event_name == "issues" and payload.get("action") == "closed":
        handle_issue_closed(repo_full_name, issue)
        return jsonify({"ok": True, "handled": "issue closed"}), 200

    if event_name == "issues" and payload.get("action") == "reopened":
        upsert_issue_record(
            repo_full_name,
            int(issue_number),
            issue.get("title") or f"Issue #{issue_number}",
            "open",
        )

    should_run, reason = webhook_decision(payload, event_name)
    if not should_run:
        return jsonify({"ok": True, "ignored": reason}), 200

    job_id = queue_payload(payload, reason)
    return jsonify({"ok": True, "queued": True, "job_id": job_id, "reason": reason}), 202


def default_env_file_path() -> Path:
    raw = os.getenv("CODING_BOT_ENV_FILE", Path(__file__).resolve().with_name(".env"))
    return Path(raw).expanduser().resolve(strict=False)


def initialize_runtime(env_file: Path) -> None:
    global CONFIG
    load_env_file(env_file)
    CONFIG = read_config(env_file)
    CONFIG["env_file"] = str(env_file)
    RUNTIME["started_at"] = now_utc()
    validate_config(CONFIG)
    ensure_dir(CONFIG["job_root"])
    ensure_dir(CONFIG["repo_root"])
    ensure_dir(CONFIG["active_dir"])
    ensure_dir(Path(CONFIG["state_file"]).parent)
    sync_codex_runtime_home(CONFIG)
    init_db()


def bootstrap_service(env_file: Path | None = None) -> None:
    global SERVICE_BOOTSTRAPPED
    actual_env_file = (env_file or default_env_file_path()).expanduser().resolve(strict=False)
    with SERVICE_BOOT_LOCK:
        initialize_runtime(actual_env_file)
        if SERVICE_BOOTSTRAPPED:
            return
        recover_inflight_jobs()
        start_dispatch_thread()
        start_polling_thread()
        SERVICE_BOOTSTRAPPED = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex GitHub issue bot service")
    parser.add_argument(
        "--env-file",
        default=str(Path(__file__).resolve().with_name(".env")),
        help="Path to env file",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve")
    subparsers.add_parser("poll-once")
    subparsers.add_parser("doctor")
    run_job = subparsers.add_parser("run-job")
    run_job.add_argument("job_id")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env_file = Path(args.env_file).expanduser().resolve(strict=False)
    load_env_file(env_file)
    CONFIG.update(read_config(env_file))
    CONFIG["env_file"] = str(env_file)
    RUNTIME["started_at"] = now_utc()

    if args.command == "doctor":
        raise SystemExit(run_doctor(CONFIG, env_file))

    initialize_runtime(env_file)

    if args.command == "serve":
        bootstrap_service(env_file)
        APP.run(host=CONFIG["listen_host"], port=CONFIG["listen_port"], threaded=True)
        return

    if args.command == "poll-once":
        recover_inflight_jobs()
        poll_once()
        dispatch_queued_jobs()
        return

    if args.command == "run-job":
        process_job(args.job_id)
        return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
