#!/usr/bin/env python3
import argparse
import base64
import copy
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
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from flask import Flask, jsonify, request


APP_DIR = Path(__file__).resolve().parent
DEFAULT_FORK_OWNER = "YeYing2025"
DEFAULT_EXECUTION_BACKEND = "openclaw"
SUPPORTED_EXECUTION_BACKENDS = {"openclaw"}
ACTIVE_JOB_STATUSES = {"queued", "running"}

CONFIG: dict[str, Any] = {}
APP = Flask(__name__)
STATE_LOCK = threading.Lock()
DB_LOCK = threading.Lock()
OPENCLAW_CONFIG_LOCK = threading.Lock()
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
FEISHU_API_BASE_URL = "https://open.feishu.cn/open-apis"
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


def env_csv(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return list(default)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or list(default)


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


def prefer_existing_path(preferred: Path, legacy: Path | None = None) -> Path:
    if preferred.exists():
        return preferred.resolve(strict=False)
    if legacy is not None and legacy.exists():
        return legacy.resolve(strict=False)
    return preferred.resolve(strict=False)


def prefer_existing_command(preferred: str, legacy: str | None, *, base_dir: Path) -> str:
    preferred_path = base_dir / preferred
    if preferred_path.exists():
        return preferred
    if legacy:
        legacy_path = base_dir / legacy
        if legacy_path.exists():
            return legacy
    return preferred


def read_config(env_file_path: Path | None = None) -> dict[str, Any]:
    default_env_path = prefer_existing_path(
        APP_DIR / "config" / "coder-bot.env",
        APP_DIR / ".env",
    )
    actual_env_path = (env_file_path or default_env_path).expanduser().resolve(strict=False)
    env_base_dir = actual_env_path.parent
    default_app_home = ".." if env_base_dir.name == "config" else "."
    app_home = resolve_path_value(os.getenv("APP_HOME", default_app_home), base_dir=env_base_dir)
    data_dir = resolve_path_value(os.getenv("DATA_DIR", "data"), base_dir=app_home)
    secrets_dir = resolve_path_value(os.getenv("SECRETS_DIR", "secrets"), base_dir=app_home)
    default_openclaw_bin = prefer_existing_command(
        "scripts/openclaw-local",
        "openclaw-local",
        base_dir=app_home,
    )
    default_openclaw_config_path = (app_home / "config" / "openclaw.json").resolve(strict=False)
    default_openclaw_runtime_config_path = (
        data_dir / "openclaw" / "runtime" / "openclaw.runtime.json"
    ).resolve(strict=False)
    default_openclaw_state_dir = prefer_existing_path(
        data_dir / "openclaw" / "state",
        app_home / "feishu-state",
    )
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
        "execution_backend": (
            os.getenv("EXECUTION_BACKEND", DEFAULT_EXECUTION_BACKEND).strip().lower()
            or DEFAULT_EXECUTION_BACKEND
        ),
        "openclaw_bin": env_command("OPENCLAW_BIN", default_openclaw_bin, base_dir=app_home),
        "openclaw_model": os.getenv("OPENCLAW_MODEL", "router/gpt-5.4").strip()
        or "router/gpt-5.4",
        "openclaw_timeout": env_int("OPENCLAW_TIMEOUT", 1800),
        "openclaw_config_path": env_path(
            "OPENCLAW_CONFIG_PATH",
            default_openclaw_config_path,
            base_dir=app_home,
        ),
        "openclaw_runtime_config_path": env_path(
            "OPENCLAW_RUNTIME_CONFIG_PATH",
            default_openclaw_runtime_config_path,
            base_dir=app_home,
        ),
        "openclaw_state_dir": env_path(
            "OPENCLAW_STATE_DIR",
            default_openclaw_state_dir,
            base_dir=app_home,
        ),
        "openclaw_session_prefix": os.getenv("OPENCLAW_SESSION_PREFIX", "gh").strip() or "gh",
        "issue_branch_prefix": os.getenv("ISSUE_BRANCH_PREFIX", "coder").strip() or "coder",
        "feishu_handoff_chat_id": os.getenv("FEISHU_HANDOFF_CHAT_ID", "").strip(),
        "feishu_account_id": os.getenv("FEISHU_ACCOUNT_ID", "default").strip() or "default",
        "feishu_confirm_keywords": env_csv(
            "FEISHU_CONFIRM_KEYWORDS",
            ["/run", "开始执行", "确认执行", "可以执行"],
        ),
        "feishu_thread_scan_limit": env_int("FEISHU_THREAD_SCAN_LIMIT", 30),
        "db_path": env_path(
            "DB_PATH",
            data_dir / "issue_bot.db",
            base_dir=app_home,
        ),
        "repo_root": env_path(
            "REPO_ROOT",
            data_dir / "repos",
            base_dir=app_home,
        ),
        "repo_lock_wait_seconds": env_int("REPO_LOCK_WAIT_SECONDS", 7200),
        "job_root": env_path(
            "JOB_ROOT",
            data_dir / "jobs",
            base_dir=app_home,
        ),
        "active_dir": env_path(
            "ACTIVE_DIR",
            data_dir / "active",
            base_dir=app_home,
        ),
        "sync_script_path": os.getenv("SYNC_SCRIPT_PATH", "scripts/sync.sh").strip(),
        "git_author_name": os.getenv("GIT_AUTHOR_NAME", service_actor_name()).strip()
        or service_actor_name(),
        "git_author_email": os.getenv("GIT_AUTHOR_EMAIL", DEFAULT_GIT_AUTHOR_EMAIL).strip()
        or DEFAULT_GIT_AUTHOR_EMAIL,
        "pr_title_prefix": os.getenv("PR_TITLE_PREFIX", "[Coder]").strip() or "[Coder]",
        "submit_comment_after_pr": env_bool("SUBMIT_COMMENT_AFTER_PR", True),
        "submit_comment_body": os.getenv("SUBMIT_COMMENT_BODY", "/submit").strip() or "/submit",
        "default_base_branch": os.getenv("DEFAULT_BASE_BRANCH", "").strip(),
        "test_command": os.getenv("TEST_COMMAND", "").strip(),
        "state_file": env_path(
            "STATE_FILE",
            data_dir / "state.json",
            base_dir=app_home,
        ),
        "log_dir": env_path(
            "LOG_DIR",
            data_dir / "logs",
            base_dir=app_home,
        ),
        "poll_enabled": env_bool("ENABLE_POLLING", True),
        "poll_interval_seconds": env_int("POLL_INTERVAL_SECONDS", 60),
        "dispatch_interval_seconds": env_int("DISPATCH_INTERVAL_SECONDS", 5),
        "issue_scan_limit": env_int("ISSUE_SCAN_LIMIT", 30),
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


def service_actor_name() -> str:
    return "Coder"


def backend_label(config: dict[str, Any]) -> str:
    return "OpenClaw"


def backend_model_label(config: dict[str, Any]) -> str:
    return config["openclaw_model"]


def build_openclaw_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    runtime_path, _ = ensure_openclaw_runtime_config(config)
    env["OPENCLAW_CONFIG_PATH"] = str(runtime_path)
    env["OPENCLAW_STATE_DIR"] = config["openclaw_state_dir"]
    return env


def resolve_secret_input(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if not isinstance(raw, dict):
        return ""
    if str(raw.get("source") or "").strip() != "env":
        return ""
    env_key = str(raw.get("id") or "").strip()
    if not env_key:
        return ""
    return os.getenv(env_key, "").strip()


def openclaw_static_config_path(config: dict[str, Any]) -> Path:
    return Path(config["openclaw_config_path"])


def openclaw_runtime_config_path(config: dict[str, Any]) -> Path:
    return Path(config["openclaw_runtime_config_path"])


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} 必须是 JSON object: {path}")
    return payload


def write_json_object(path: Path, payload: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def clone_json_value(value: Any) -> Any:
    return copy.deepcopy(value)


def normalize_openclaw_static_config_placeholders(config: dict[str, Any]) -> None:
    path = openclaw_static_config_path(config)
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if "__APP_DIR__" not in text:
        return
    path.write_text(
        text.replace("__APP_DIR__", str(Path(config["app_home"]).resolve(strict=False))),
        encoding="utf-8",
    )


def extract_openclaw_runtime_sections(payload: dict[str, Any]) -> dict[str, list[Any] | None]:
    agents_list: list[Any] | None = None
    agents = payload.get("agents")
    if isinstance(agents, dict) and isinstance(agents.get("list"), list):
        agents_list = clone_json_value(agents["list"])

    bindings: list[Any] | None = None
    if isinstance(payload.get("bindings"), list):
        bindings = clone_json_value(payload["bindings"])

    return {
        "agents_list": agents_list,
        "bindings": bindings,
    }


def strip_openclaw_runtime_sections(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = clone_json_value(payload)
    agents = cleaned.get("agents")
    if isinstance(agents, dict) and "list" in agents:
        next_agents = clone_json_value(agents)
        next_agents.pop("list", None)
        cleaned["agents"] = next_agents
    cleaned.pop("bindings", None)
    return cleaned


def apply_openclaw_runtime_sections(
    static_payload: dict[str, Any],
    sections: dict[str, list[Any] | None],
) -> dict[str, Any]:
    merged = clone_json_value(static_payload)

    agents_list = sections.get("agents_list")
    if agents_list is not None:
        agents = merged.get("agents")
        next_agents = clone_json_value(agents) if isinstance(agents, dict) else {}
        next_agents["list"] = clone_json_value(agents_list)
        merged["agents"] = next_agents

    bindings = sections.get("bindings")
    if bindings is not None:
        merged["bindings"] = clone_json_value(bindings)
    else:
        merged.pop("bindings", None)

    return merged


def ensure_openclaw_runtime_config(config: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    static_path = openclaw_static_config_path(config)
    runtime_path = openclaw_runtime_config_path(config)

    with OPENCLAW_CONFIG_LOCK:
        normalize_openclaw_static_config_placeholders(config)
        static_payload = load_json_object(static_path, label="OpenClaw 静态配置")
        cleaned_static = strip_openclaw_runtime_sections(static_payload)

        runtime_payload: dict[str, Any] | None = None
        if runtime_path.exists():
            runtime_payload = load_json_object(runtime_path, label="OpenClaw 运行时配置")

        static_sections = extract_openclaw_runtime_sections(static_payload)
        runtime_sections = extract_openclaw_runtime_sections(runtime_payload or {})
        merged_sections = {
            "agents_list": runtime_sections["agents_list"]
            if runtime_sections["agents_list"] is not None
            else static_sections["agents_list"],
            "bindings": runtime_sections["bindings"]
            if runtime_sections["bindings"] is not None
            else static_sections["bindings"],
        }

        if cleaned_static != static_payload:
            write_json_object(static_path, cleaned_static)

        effective_payload = apply_openclaw_runtime_sections(cleaned_static, merged_sections)
        if runtime_payload != effective_payload:
            write_json_object(runtime_path, effective_payload)

        return runtime_path, effective_payload


def load_openclaw_config_json(config: dict[str, Any]) -> dict[str, Any]:
    _, payload = ensure_openclaw_runtime_config(config)
    return payload


def save_openclaw_config_json(config: dict[str, Any], payload: dict[str, Any]) -> None:
    static_path = openclaw_static_config_path(config)
    runtime_path = openclaw_runtime_config_path(config)
    desired_sections = extract_openclaw_runtime_sections(payload)
    agents_value = payload.get("agents")
    agents_explicit = isinstance(agents_value, dict) and "list" in agents_value
    bindings_explicit = "bindings" in payload

    with OPENCLAW_CONFIG_LOCK:
        static_payload = load_json_object(static_path, label="OpenClaw 静态配置")
        cleaned_static = strip_openclaw_runtime_sections(static_payload)
        if cleaned_static != static_payload:
            write_json_object(static_path, cleaned_static)

        current_runtime_payload: dict[str, Any] | None = None
        if runtime_path.exists():
            current_runtime_payload = load_json_object(runtime_path, label="OpenClaw 运行时配置")
        current_sections = extract_openclaw_runtime_sections(current_runtime_payload or {})

        merged_sections = {
            "agents_list": desired_sections["agents_list"]
            if agents_explicit
            else current_sections["agents_list"],
            "bindings": desired_sections["bindings"]
            if bindings_explicit
            else current_sections["bindings"],
        }
        write_json_object(
            runtime_path,
            apply_openclaw_runtime_sections(cleaned_static, merged_sections),
        )


def openclaw_issue_workspace_dir(config: dict[str, Any], repo_full_name: str, issue_number: int) -> Path:
    # OpenClaw 的长期工作区放在 issue 根目录。
    # 后续仓库会克隆到这个目录下的 repo/ 子目录里，这样飞书讨论阶段和真正执行阶段
    # 都能复用同一个 agent/workspace，而不会因为仓库尚未克隆而丢失会话。
    return ensure_dir(repo_issue_root(config, repo_full_name, issue_number))


def build_feishu_thread_peer_id(chat_id: str, thread_id: str) -> str:
    return f"{chat_id.strip()}:topic:{thread_id.strip()}".lower()


def build_feishu_thread_session_key(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    chat_id: str,
    thread_id: str,
) -> str:
    agent_id = build_issue_agent_id(config, repo_full_name, issue_number).lower()
    peer_id = build_feishu_thread_peer_id(chat_id, thread_id)
    return f"agent:{agent_id}:feishu:thread:{peer_id}"


def is_feishu_route_session_key(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized.startswith("agent:") and (
        ":feishu:group:" in normalized or ":feishu:thread:" in normalized
    )


def resolve_feishu_runtime_settings(config: dict[str, Any]) -> dict[str, str]:
    payload = load_openclaw_config_json(config)
    feishu_cfg = ((payload.get("channels") or {}).get("feishu") or {})
    if not isinstance(feishu_cfg, dict):
        feishu_cfg = {}

    app_id = str(feishu_cfg.get("appId") or os.getenv("FEISHU_APP_ID", "")).strip()
    app_secret = resolve_secret_input(feishu_cfg.get("appSecret")) or os.getenv("FEISHU_APP_SECRET", "").strip()

    configured_groups = [
        str(item).strip()
        for item in (feishu_cfg.get("groupAllowFrom") or [])
        if str(item).strip()
    ]
    chat_id = config["feishu_handoff_chat_id"] or (configured_groups[0] if configured_groups else "")
    account_id = config["feishu_account_id"]

    if not app_id or not app_secret:
        raise RuntimeError("Feishu appId/appSecret is missing from OpenClaw config or env")
    if not chat_id:
        raise RuntimeError("FEISHU_HANDOFF_CHAT_ID is empty and channels.feishu.groupAllowFrom has no group")

    return {
        "app_id": app_id,
        "app_secret": app_secret,
        "chat_id": chat_id,
        "account_id": account_id,
    }


def get_feishu_tenant_access_token(config: dict[str, Any]) -> str:
    cached_token = str(RUNTIME.get("feishu_access_token") or "").strip()
    expires_at = float(RUNTIME.get("feishu_access_token_expires_at") or 0)
    if cached_token and expires_at > time.time() + 30:
        return cached_token

    settings = resolve_feishu_runtime_settings(config)
    response = requests.post(
        f"{FEISHU_API_BASE_URL}/auth/v3/tenant_access_token/internal",
        json={"app_id": settings["app_id"], "app_secret": settings["app_secret"]},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if int(payload.get("code") or 0) != 0:
        raise RuntimeError(f"Feishu tenant token failed: {payload.get('msg') or payload}")

    token = str(payload.get("tenant_access_token") or "").strip()
    expire_seconds = int(payload.get("expire") or 7200)
    if not token:
        raise RuntimeError("Feishu tenant token response is missing tenant_access_token")

    RUNTIME["feishu_access_token"] = token
    RUNTIME["feishu_access_token_expires_at"] = time.time() + max(60, expire_seconds - 120)
    return token


def feishu_request(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = get_feishu_tenant_access_token(config)
    response = requests.request(
        method,
        f"{FEISHU_API_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=json_body,
        params=params,
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = short_text(response.text or "", 2000)
        message = str(exc)
        if detail:
            message = f"{message}\n{detail}"
        raise requests.HTTPError(message, response=response) from None
    payload = response.json()
    if int(payload.get("code") or 0) != 0:
        raise RuntimeError(f"Feishu API {path} failed: {payload.get('msg') or payload}")
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def parse_feishu_message_text(item: dict[str, Any]) -> str:
    msg_type = str(item.get("msg_type") or "text").strip()
    raw_content = str(((item.get("body") or {}).get("content")) or "")
    if not raw_content:
        return ""
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        return raw_content.strip()
    if msg_type == "text":
        return str(parsed.get("text") or "").strip()
    if isinstance(parsed, str):
        return parsed.strip()
    return str(parsed.get("text") or parsed.get("title") or "").strip()


def feishu_get_message(config: dict[str, Any], message_id: str) -> dict[str, Any]:
    payload = feishu_request(config, "GET", f"/im/v1/messages/{message_id}")
    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, list) and items:
        item = items[0]
        if isinstance(item, dict):
            return item
    if isinstance(payload, dict):
        return payload
    raise RuntimeError(f"Feishu message lookup returned empty item for {message_id}")


def feishu_send_text_message(config: dict[str, Any], chat_id: str, text: str) -> str:
    payload = feishu_request(
        config,
        "POST",
        "/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        json_body={
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        },
    )
    message_id = str(payload.get("message_id") or "").strip()
    if not message_id:
        raise RuntimeError("Feishu send message succeeded but message_id is missing")
    return message_id


def feishu_reply_in_thread(config: dict[str, Any], root_message_id: str, text: str) -> str:
    payload = feishu_request(
        config,
        "POST",
        f"/im/v1/messages/{root_message_id}/reply",
        json_body={
            "content": json.dumps({"text": text}, ensure_ascii=False),
            "msg_type": "text",
            "reply_in_thread": True,
        },
    )
    message_id = str(payload.get("message_id") or "").strip()
    if not message_id:
        raise RuntimeError("Feishu thread reply succeeded but message_id is missing")
    return message_id


def feishu_list_thread_messages(config: dict[str, Any], thread_id: str, limit: int) -> list[dict[str, Any]]:
    payload = feishu_request(
        config,
        "GET",
        "/im/v1/messages",
        params={
            "container_id_type": "thread",
            "container_id": thread_id,
            "sort_type": "ByCreateTimeDesc",
            "page_size": min(max(1, limit), 50),
        },
    )
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []

    messages: list[dict[str, Any]] = []
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        sender = raw_item.get("sender") or {}
        messages.append(
            {
                "message_id": str(raw_item.get("message_id") or "").strip(),
                "thread_id": str(raw_item.get("thread_id") or "").strip(),
                "create_time": int(str(raw_item.get("create_time") or "0") or "0"),
                "sender_id": str(sender.get("id") or "").strip(),
                "sender_type": str(sender.get("sender_type") or "").strip(),
                "content": parse_feishu_message_text(raw_item),
            }
        )
    messages.sort(key=lambda item: (int(item["create_time"]), str(item["message_id"])))
    return messages


def feishu_message_marker_is_newer(
    message: dict[str, Any],
    last_seen_time: str | None,
    last_seen_message_id: str | None,
) -> bool:
    current_time = int(str(message.get("create_time") or "0") or "0")
    seen_time = int(str(last_seen_time or "0") or "0")
    if current_time != seen_time:
        return current_time > seen_time
    return str(message.get("message_id") or "") > str(last_seen_message_id or "")


def normalize_confirm_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def message_matches_confirm_keywords(text: str, keywords: list[str]) -> bool:
    normalized_text = normalize_confirm_text(text)
    if not normalized_text:
        return False
    lines = [normalize_confirm_text(line) for line in text.splitlines() if line.strip()]
    candidates = [normalized_text, *lines]
    for keyword in keywords:
        normalized_keyword = normalize_confirm_text(keyword)
        if not normalized_keyword:
            continue
        for candidate in candidates:
            if candidate == normalized_keyword or candidate.startswith(f"{normalized_keyword} "):
                return True
    return False


def feishu_group_message_scope_missing(exc: Exception) -> bool:
    text = str(exc or "")
    return "im:message.group_msg" in text or "code\":230027" in text


def append_note_marker(note: str | None, marker: str) -> str:
    current = str(note or "").strip()
    if not current:
        return marker
    if marker in current:
        return current
    return f"{current} | {marker}"


def build_feishu_handoff_intro(repo_full_name: str, issue: dict[str, Any]) -> str:
    title = short_text(issue.get("title") or f"Issue #{issue['number']}", 120)
    return textwrap.dedent(
        f"""
        [Coder] GitHub 已接收 {repo_full_name}#{issue['number']}
        标题：{title}

        请在线程里先讨论方案。
        确认开始执行后，再在线程里发送 `/run`。
        """
    ).strip()


def preferred_issue_binding(
    repo_full_name: str,
    issue_number: int,
    *,
    chat_id: str | None = None,
) -> sqlite3.Row | None:
    bindings = list_issue_bindings(repo_full_name, issue_number)
    if not bindings:
        return None
    if chat_id:
        for binding in bindings:
            if str(binding["chat_id"]) == chat_id:
                return binding
    return bindings[0]


def remove_openclaw_feishu_route_bindings(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    bindings: list[sqlite3.Row] | None = None,
) -> None:
    issue_bindings = bindings or list_issue_bindings(repo_full_name, issue_number)
    if not issue_bindings:
        return

    peer_ids = {
        build_feishu_thread_peer_id(str(row["chat_id"]), str(row["thread_id"]))
        for row in issue_bindings
    }
    agent_id = build_issue_agent_id(config, repo_full_name, issue_number)
    payload = load_openclaw_config_json(config)
    existing = payload.get("bindings")
    if not isinstance(existing, list):
        return

    next_bindings: list[dict[str, Any]] = []
    changed = False
    for raw_binding in existing:
        if not isinstance(raw_binding, dict):
            next_bindings.append(raw_binding)
            continue
        match = raw_binding.get("match") or {}
        peer = match.get("peer") or {}
        should_remove = (
            raw_binding.get("type", "route") == "route"
            and str(raw_binding.get("agentId") or "") == agent_id
            and str(match.get("channel") or "") == "feishu"
            and str(peer.get("kind") or "") == "group"
            and str(peer.get("id") or "").lower() in peer_ids
        )
        if should_remove:
            changed = True
            continue
        next_bindings.append(raw_binding)

    if changed:
        payload["bindings"] = next_bindings or None
        save_openclaw_config_json(config, payload)


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
        "User-Agent": "coder-issue-bot",
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
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = tail_text(response.text or "", 2000)
        message = str(exc)
        if detail:
            message = f"{message}\n{detail}"
        raise requests.HTTPError(message, response=response) from None
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


def openclaw_provider_api_key_configured(config: dict[str, Any], provider_id: str) -> tuple[bool, str]:
    config_path = config.get("openclaw_config_path")
    if not config_path or not path_readable(str(config_path)):
        return False, "OpenClaw config not readable"
    try:
        payload = json.loads(Path(str(config_path)).read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"OpenClaw config parse failed: {exc}"
    providers = (((payload.get("models") or {}).get("providers")) or {})
    if not isinstance(providers, dict):
        return False, "models.providers is missing"
    provider = providers.get(provider_id) or {}
    if not isinstance(provider, dict):
        return False, f"provider `{provider_id}` is missing"
    api_key = provider.get("apiKey")
    if isinstance(api_key, str):
        return bool(api_key.strip()), f"models.providers.{provider_id}.apiKey"
    if isinstance(api_key, dict):
        if str(api_key.get("source") or "").strip() == "env":
            env_key = str(api_key.get("id") or "").strip()
            if not env_key:
                return False, f"models.providers.{provider_id}.apiKey env id is empty"
            return bool(os.getenv(env_key, "").strip()), env_key
        # 其他 SecretRef 交给 OpenClaw 自己解析，这里只要有结构就视为已配置。
        return True, f"models.providers.{provider_id}.apiKey"
    return False, f"models.providers.{provider_id}.apiKey"


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
    backend = config["execution_backend"]

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

    if not config["github_clone_ssh_key_path"]:
        errors.append("GITHUB_CLONE_SSH_KEY_PATH 不能为空。")
    elif not path_readable(config["github_clone_ssh_key_path"]):
        errors.append("GITHUB_CLONE_SSH_KEY_PATH 指向的 SSH 私钥不存在或不可读。")

    if backend not in SUPPORTED_EXECUTION_BACKENDS:
        errors.append(
            "EXECUTION_BACKEND 只支持："
            + "、".join(sorted(SUPPORTED_EXECUTION_BACKENDS))
        )

    if not config["openclaw_bin"]:
        errors.append("OPENCLAW_BIN 不能为空。")
    elif not command_exists(config["openclaw_bin"]):
        errors.append("OPENCLAW_BIN 不存在或不可执行。")

    if not config["openclaw_model"]:
        errors.append("OPENCLAW_MODEL 不能为空。")

    if not config["openclaw_config_path"]:
        errors.append("OPENCLAW_CONFIG_PATH 不能为空。")
    elif not path_readable(config["openclaw_config_path"]):
        errors.append("OPENCLAW_CONFIG_PATH 指向的配置文件不存在或不可读。")
    elif str(config["openclaw_model"]).startswith("router/"):
        api_key_ok, api_key_detail = openclaw_provider_api_key_configured(config, "router")
        if not api_key_ok:
            errors.append(f"router provider 缺少可用 apiKey：{api_key_detail}")

    if not config["openclaw_runtime_config_path"]:
        errors.append("OPENCLAW_RUNTIME_CONFIG_PATH 不能为空。")

    if not config["trigger_label"] and not config["run_on_issue_opened"]:
        errors.append("至少需要保留一种触发方式：TRIGGER_LABEL 或 RUN_ON_ISSUE_OPENED=true。")

    return errors


def validate_config(config: dict[str, Any]) -> None:
    errors = collect_config_errors(config)
    if errors:
        raise SystemExit("配置校验失败：\n- " + "\n- ".join(errors))


def run_doctor(config: dict[str, Any], env_file: Path) -> int:
    results: list[tuple[str, bool, str]] = []
    backend = config["execution_backend"]

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

    check("执行后端", backend in SUPPORTED_EXECUTION_BACKENDS, backend)

    openclaw_exists = command_exists(config["openclaw_bin"])
    check("OpenClaw 可执行文件", openclaw_exists, config["openclaw_bin"])
    if openclaw_exists:
        try:
            openclaw_probe = run_command(
                [config["openclaw_bin"], "--version"],
                cwd=Path(config["app_home"]),
                timeout=30,
            )
            probe_output = tail_text(
                "\n".join(part for part in [openclaw_probe.stdout, openclaw_probe.stderr] if part),
                500,
            ) or config["openclaw_bin"]
            check("OpenClaw CLI 可运行", openclaw_probe.returncode == 0, probe_output)
        except Exception as exc:
            check("OpenClaw CLI 可运行", False, str(exc))
    else:
        check("OpenClaw CLI 可运行", False, "skipped: executable missing")

    check(
        "OpenClaw 静态配置",
        path_readable(config["openclaw_config_path"]),
        config["openclaw_config_path"],
    )
    if openclaw_exists and path_readable(config["openclaw_config_path"]):
        try:
            runtime_path, _ = ensure_openclaw_runtime_config(config)
            check("OpenClaw 运行时配置", True, str(runtime_path))
            agents = list_openclaw_agents(config)
            check("OpenClaw Agent Registry", True, f"{len(agents)} agents")
        except Exception as exc:
            check("OpenClaw 运行时配置", False, str(exc))
            check("OpenClaw Agent Registry", False, str(exc))
    else:
        check("OpenClaw 运行时配置", False, "skipped: static config or executable missing")
        check("OpenClaw Agent Registry", False, "skipped: config or executable missing")

    check("GitHub CLI", command_exists("gh"), "gh")
    check(
        "Gunicorn",
        importlib.util.find_spec("gunicorn") is not None or command_exists("gunicorn"),
        "gunicorn",
    )

    try:
        ensure_writable_path(Path(config["db_path"]), is_file=True)
        ensure_writable_path(Path(config["job_root"]), is_file=False)
        ensure_writable_path(Path(config["repo_root"]), is_file=False)
        ensure_writable_path(Path(config["active_dir"]), is_file=False)
        ensure_writable_path(Path(config["state_file"]), is_file=True)
        ensure_writable_path(Path(config["log_dir"]), is_file=False)
        ensure_writable_path(Path(config["openclaw_runtime_config_path"]), is_file=True)
        ensure_writable_path(Path(config["openclaw_state_dir"]), is_file=False)
        detail = (
            "DB_PATH / JOB_ROOT / REPO_ROOT / ACTIVE_DIR / STATE_FILE / "
            "LOG_DIR / OPENCLAW_RUNTIME_CONFIG_PATH / OPENCLAW_STATE_DIR 可写"
        )
        check("目录写权限", True, detail)
    except Exception as exc:
        check("目录写权限", False, str(exc))

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

    print("Coder Bot Doctor")
    print(f"APP_HOME: {config['app_home']}")
    print(f"DATA_DIR: {config['data_dir']}")
    print(f"SECRETS_DIR: {config['secrets_dir']}")
    print(f"EXECUTION_BACKEND: {backend}")
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

                -- 这张表记录每个 issue 对应的长期会话状态：
                -- 一个 GitHub Issue 对应一个可持续复用的会话。
                CREATE TABLE IF NOT EXISTS issue_sessions (
                    repo_full_name TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    backend TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    session_state TEXT NOT NULL,
                    last_trigger_reason TEXT,
                    last_triggered_at TEXT,
                    handoff_prompt TEXT,
                    agent_session_id TEXT,
                    branch_name TEXT NOT NULL,
                    pr_url TEXT,
                    summary TEXT,
                    last_result_status TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (repo_full_name, issue_number)
                );

                CREATE INDEX IF NOT EXISTS idx_issue_sessions_backend_updated
                ON issue_sessions (backend, updated_at);

                -- 一个 issue 会话可以绑定到一个或多个飞书线程，
                -- 这样 OpenClaw 就能在同一上下文里继续处理。
                CREATE TABLE IF NOT EXISTS feishu_bindings (
                    chat_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    repo_full_name TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    session_key TEXT NOT NULL,
                    binding_state TEXT NOT NULL,
                    note TEXT,
                    root_message_id TEXT,
                    prompt_message_id TEXT,
                    last_seen_message_id TEXT,
                    last_seen_message_time TEXT,
                    confirm_message_id TEXT,
                    confirm_message_time TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, thread_id)
                );

                CREATE INDEX IF NOT EXISTS idx_feishu_bindings_issue
                ON feishu_bindings (repo_full_name, issue_number, updated_at);
                """
            )
            migrate_db_schema(connection)


def table_columns(connection: sqlite3.Connection, table_name: str) -> dict[str, sqlite3.Row]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]): row for row in rows}


def add_column_if_missing(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> None:
    columns = table_columns(connection, table_name)
    if column_name in columns:
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def repair_issue_session_keys(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT repo_full_name, issue_number, session_key
        FROM issue_sessions
        """
    ).fetchall()
    updates: list[tuple[str, str, int]] = []
    for row in rows:
        repo_full_name = str(row["repo_full_name"] or "").strip()
        if not repo_full_name:
            continue
        issue_number = int(row["issue_number"])
        current_session_key = str(row["session_key"] or "").strip()
        normalized_session_key = normalize_issue_session_key(
            CONFIG,
            repo_full_name,
            issue_number,
            current_session_key,
        )
        if normalized_session_key != current_session_key:
            updates.append((normalized_session_key, repo_full_name, issue_number))

    if updates:
        connection.executemany(
            """
            UPDATE issue_sessions
            SET session_key = ?
            WHERE repo_full_name = ? AND issue_number = ?
            """,
            updates,
        )


def migrate_issue_sessions_table(connection: sqlite3.Connection) -> None:
    columns = table_columns(connection, "issue_sessions")
    if not columns:
        return

    add_column_if_missing(connection, "issue_sessions", "session_state", "TEXT NOT NULL DEFAULT 'triggered'")
    add_column_if_missing(connection, "issue_sessions", "last_trigger_reason", "TEXT")
    add_column_if_missing(connection, "issue_sessions", "last_triggered_at", "TEXT")
    add_column_if_missing(connection, "issue_sessions", "handoff_prompt", "TEXT")
    add_column_if_missing(connection, "issue_sessions", "summary", "TEXT")
    add_column_if_missing(connection, "issue_sessions", "last_result_status", "TEXT")
    add_column_if_missing(connection, "issue_sessions", "agent_session_id", "TEXT")
    add_column_if_missing(connection, "issue_sessions", "pr_url", "TEXT")

    columns = table_columns(connection, "issue_sessions")
    if "plan_summary" in columns and "summary" in columns:
        connection.execute(
            """
            UPDATE issue_sessions
            SET summary = COALESCE(summary, plan_summary)
            WHERE plan_summary IS NOT NULL
            """
        )

    if "branch_name" not in columns:
        add_column_if_missing(connection, "issue_sessions", "branch_name", "TEXT NOT NULL DEFAULT 'coder/unknown'")

    if "backend" not in columns:
        add_column_if_missing(connection, "issue_sessions", "backend", "TEXT NOT NULL DEFAULT 'openclaw'")

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_issue_sessions_backend_updated
        ON issue_sessions (backend, updated_at)
        """
    )
    repair_issue_session_keys(connection)


def migrate_feishu_bindings_table(connection: sqlite3.Connection) -> None:
    columns = table_columns(connection, "feishu_bindings")
    if not columns:
        return
    add_column_if_missing(connection, "feishu_bindings", "binding_state", "TEXT NOT NULL DEFAULT 'bound'")
    add_column_if_missing(connection, "feishu_bindings", "note", "TEXT")
    add_column_if_missing(connection, "feishu_bindings", "root_message_id", "TEXT")
    add_column_if_missing(connection, "feishu_bindings", "prompt_message_id", "TEXT")
    add_column_if_missing(connection, "feishu_bindings", "last_seen_message_id", "TEXT")
    add_column_if_missing(connection, "feishu_bindings", "last_seen_message_time", "TEXT")
    add_column_if_missing(connection, "feishu_bindings", "confirm_message_id", "TEXT")
    add_column_if_missing(connection, "feishu_bindings", "confirm_message_time", "TEXT")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_feishu_bindings_issue
        ON feishu_bindings (repo_full_name, issue_number, updated_at)
        """
    )


def migrate_db_schema(connection: sqlite3.Connection) -> None:
    migrate_issue_sessions_table(connection)
    migrate_feishu_bindings_table(connection)


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


def build_issue_session_key(config: dict[str, Any], repo_full_name: str, issue_number: int) -> str:
    prefix = slugify(config.get("openclaw_session_prefix", "gh"), limit=16)
    repo_slug = slugify(repo_full_name.replace("/", "-"), limit=48)
    if prefix:
        return f"{prefix}-{repo_slug}-issue-{issue_number}"
    return f"{repo_slug}-issue-{issue_number}"


def normalize_issue_session_key(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    session_key: str | None,
) -> str:
    expected = build_issue_session_key(config, repo_full_name, issue_number)
    candidate = str(session_key or "").strip()
    if not candidate or is_feishu_route_session_key(candidate):
        return expected
    return candidate


def build_issue_branch_name(config: dict[str, Any], issue_number: int, issue_title: str) -> str:
    prefix = slugify(config.get("issue_branch_prefix", "coder"), limit=16) or "coder"
    title_slug = slugify(issue_title or "task", limit=32)
    return f"{prefix}/issue-{issue_number}-{title_slug}"


def build_issue_agent_id(config: dict[str, Any], repo_full_name: str, issue_number: int) -> str:
    prefix = slugify(config.get("openclaw_session_prefix", "gh"), limit=12)
    repo_slug = slugify(repo_full_name.replace("/", "-"), limit=48)
    if prefix:
        return f"{prefix}-{repo_slug}-issue-{issue_number}"
    return f"{repo_slug}-issue-{issue_number}"


def get_issue_session(repo_full_name: str, issue_number: int) -> sqlite3.Row | None:
    return fetchone(
        """
        SELECT *
        FROM issue_sessions
        WHERE repo_full_name = ? AND issue_number = ?
        """,
        (repo_full_name, issue_number),
    )


def upsert_issue_session(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    *,
    backend: str | None = None,
    session_key: str | None = None,
    session_state: str | None = None,
    last_trigger_reason: str | None = None,
    last_triggered_at: str | None = None,
    handoff_prompt: str | None = None,
    agent_session_id: str | None = None,
    branch_name: str | None = None,
    pr_url: str | None = None,
    summary: str | None = None,
    last_result_status: str | None = None,
) -> sqlite3.Row:
    existing = get_issue_session(repo_full_name, issue_number)
    created_at = str(existing["created_at"]) if existing else now_utc()
    final_session_key = normalize_issue_session_key(
        config,
        repo_full_name,
        issue_number,
        session_key if session_key is not None else (str(existing["session_key"]) if existing and existing["session_key"] else None),
    )
    record = {
        "backend": backend or (str(existing["backend"]) if existing else config["execution_backend"]),
        "session_key": final_session_key,
        "session_state": session_state
        or (str(existing["session_state"]) if existing and existing["session_state"] else "triggered"),
        "last_trigger_reason": (
            last_trigger_reason
            if last_trigger_reason is not None
            else (str(existing["last_trigger_reason"]) if existing and existing["last_trigger_reason"] else None)
        ),
        "last_triggered_at": (
            last_triggered_at
            if last_triggered_at is not None
            else (str(existing["last_triggered_at"]) if existing and existing["last_triggered_at"] else None)
        ),
        "handoff_prompt": (
            short_text(handoff_prompt, 20000)
            if handoff_prompt is not None
            else (str(existing["handoff_prompt"]) if existing and existing["handoff_prompt"] else None)
        ),
        "agent_session_id": (
            agent_session_id
            if agent_session_id is not None
            else (str(existing["agent_session_id"]) if existing and existing["agent_session_id"] else None)
        ),
        "branch_name": branch_name
        or (
            str(existing["branch_name"])
            if existing
            else build_issue_branch_name(config, issue_number, f"Issue {issue_number}")
        ),
        "pr_url": pr_url if pr_url is not None else (str(existing["pr_url"]) if existing and existing["pr_url"] else None),
        "summary": (
            short_text(summary, 12000)
            if summary is not None
            else (str(existing["summary"]) if existing and existing["summary"] else None)
        ),
        "last_result_status": (
            last_result_status
            if last_result_status is not None
            else (str(existing["last_result_status"]) if existing and existing["last_result_status"] else None)
        ),
    }
    updated_at = now_utc()
    with DB_LOCK:
        with db_connect() as connection:
            connection.execute(
                """
                INSERT INTO issue_sessions (
                    repo_full_name, issue_number, backend, session_key, session_state,
                    last_trigger_reason, last_triggered_at, handoff_prompt, agent_session_id,
                    branch_name, pr_url, summary, last_result_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_full_name, issue_number) DO UPDATE SET
                    backend = excluded.backend,
                    session_key = excluded.session_key,
                    session_state = excluded.session_state,
                    last_trigger_reason = excluded.last_trigger_reason,
                    last_triggered_at = excluded.last_triggered_at,
                    handoff_prompt = excluded.handoff_prompt,
                    agent_session_id = excluded.agent_session_id,
                    branch_name = excluded.branch_name,
                    pr_url = excluded.pr_url,
                    summary = excluded.summary,
                    last_result_status = excluded.last_result_status,
                    updated_at = excluded.updated_at
                """,
                (
                    repo_full_name,
                    issue_number,
                    record["backend"],
                    record["session_key"],
                    record["session_state"],
                    record["last_trigger_reason"],
                    record["last_triggered_at"],
                    record["handoff_prompt"],
                    record["agent_session_id"],
                    record["branch_name"],
                    record["pr_url"],
                    record["summary"],
                    record["last_result_status"],
                    created_at,
                    updated_at,
                ),
            )
    return get_issue_session(repo_full_name, issue_number)  # type: ignore[return-value]


def ensure_issue_session(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    issue_title: str,
) -> sqlite3.Row:
    existing = get_issue_session(repo_full_name, issue_number)
    branch_name = str(existing["branch_name"]) if existing and existing["branch_name"] else build_issue_branch_name(
        config,
        issue_number,
        issue_title,
    )
    session_key = normalize_issue_session_key(
        config,
        repo_full_name,
        issue_number,
        str(existing["session_key"]) if existing and existing["session_key"] else None,
    )
    return upsert_issue_session(
        config,
        repo_full_name,
        issue_number,
        backend=config["execution_backend"],
        session_key=session_key,
        session_state=(
            str(existing["session_state"])
            if existing and existing["session_state"]
            else "triggered"
        ),
        branch_name=branch_name,
    )


def get_feishu_binding(chat_id: str, thread_id: str) -> sqlite3.Row | None:
    return fetchone(
        """
        SELECT *
        FROM feishu_bindings
        WHERE chat_id = ? AND thread_id = ?
        """,
        (chat_id, thread_id),
    )


def list_issue_bindings(repo_full_name: str, issue_number: int) -> list[sqlite3.Row]:
    return fetchall(
        """
        SELECT *
        FROM feishu_bindings
        WHERE repo_full_name = ? AND issue_number = ?
        ORDER BY updated_at DESC
        """,
        (repo_full_name, issue_number),
    )


def upsert_feishu_binding(
    config: dict[str, Any],
    *,
    chat_id: str,
    thread_id: str,
    repo_full_name: str,
    issue_number: int,
    session_key: str | None = None,
    note: str | None = None,
    binding_state: str = "bound",
    root_message_id: str | None = None,
    prompt_message_id: str | None = None,
    last_seen_message_id: str | None = None,
    last_seen_message_time: str | None = None,
    confirm_message_id: str | None = None,
    confirm_message_time: str | None = None,
) -> sqlite3.Row:
    if get_issue_session(repo_full_name, issue_number) is None:
        ensure_issue_session(config, repo_full_name, issue_number, f"Issue {issue_number}")
    existing = get_feishu_binding(chat_id, thread_id)
    now = now_utc()
    final_session_key = (
        session_key
        or (str(existing["session_key"]) if existing and existing["session_key"] else "")
        or build_feishu_thread_session_key(config, repo_full_name, issue_number, chat_id, thread_id)
    )
    payload = {
        "note": note if note is not None else (str(existing["note"]) if existing and existing["note"] else None),
        "root_message_id": (
            root_message_id
            if root_message_id is not None
            else (str(existing["root_message_id"]) if existing and existing["root_message_id"] else None)
        ),
        "prompt_message_id": (
            prompt_message_id
            if prompt_message_id is not None
            else (str(existing["prompt_message_id"]) if existing and existing["prompt_message_id"] else None)
        ),
        "last_seen_message_id": (
            last_seen_message_id
            if last_seen_message_id is not None
            else (str(existing["last_seen_message_id"]) if existing and existing["last_seen_message_id"] else None)
        ),
        "last_seen_message_time": (
            last_seen_message_time
            if last_seen_message_time is not None
            else (str(existing["last_seen_message_time"]) if existing and existing["last_seen_message_time"] else None)
        ),
        "confirm_message_id": (
            confirm_message_id
            if confirm_message_id is not None
            else (str(existing["confirm_message_id"]) if existing and existing["confirm_message_id"] else None)
        ),
        "confirm_message_time": (
            confirm_message_time
            if confirm_message_time is not None
            else (str(existing["confirm_message_time"]) if existing and existing["confirm_message_time"] else None)
        ),
    }
    with DB_LOCK:
        with db_connect() as connection:
            connection.execute(
                """
                INSERT INTO feishu_bindings (
                    chat_id, thread_id, repo_full_name, issue_number, session_key,
                    binding_state, note, root_message_id, prompt_message_id,
                    last_seen_message_id, last_seen_message_time, confirm_message_id,
                    confirm_message_time, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, thread_id) DO UPDATE SET
                    repo_full_name = excluded.repo_full_name,
                    issue_number = excluded.issue_number,
                    session_key = excluded.session_key,
                    binding_state = excluded.binding_state,
                    note = excluded.note,
                    root_message_id = excluded.root_message_id,
                    prompt_message_id = excluded.prompt_message_id,
                    last_seen_message_id = excluded.last_seen_message_id,
                    last_seen_message_time = excluded.last_seen_message_time,
                    confirm_message_id = excluded.confirm_message_id,
                    confirm_message_time = excluded.confirm_message_time,
                    updated_at = excluded.updated_at
                """,
                (
                    chat_id,
                    thread_id,
                    repo_full_name,
                    issue_number,
                    final_session_key,
                    binding_state,
                    payload["note"],
                    payload["root_message_id"],
                    payload["prompt_message_id"],
                    payload["last_seen_message_id"],
                    payload["last_seen_message_time"],
                    payload["confirm_message_id"],
                    payload["confirm_message_time"],
                    now,
                    now,
                ),
            )
    return get_feishu_binding(chat_id, thread_id)  # type: ignore[return-value]


def delete_feishu_binding(chat_id: str, thread_id: str) -> bool:
    existing = get_feishu_binding(chat_id, thread_id)
    if existing is None:
        return False
    execute(
        "DELETE FROM feishu_bindings WHERE chat_id = ? AND thread_id = ?",
        (chat_id, thread_id),
    )
    return True


def build_handoff_prompt(
    repo_full_name: str,
    issue: dict[str, Any],
    session_key: str | None = None,
) -> str:
    # 这里刻意生成稳定的交接提示词，保证同一个 issue 后续还能在
    # 同一个外部聊天线程里继续，而不用每次手工重建上下文。
    issue_body = short_text(issue.get("body") or "(no issue body)", 6000)
    lines = [
        f"你正在继续处理 GitHub Issue #{issue['number']}。",
        f"仓库：{repo_full_name}",
        f"标题：{issue.get('title') or f'Issue #{issue['number']}'}",
    ]
    if session_key:
        lines.append(f"会话标识：{session_key}")
    lines.extend(
        [
            "",
            "要求：",
            f"- 先用 `gh issue view {issue['number']}` 阅读最新 issue 与评论",
            "- 先在当前 Feishu 线程里沟通方案，明确边界和改动计划",
            "- 只有线程里明确确认执行后，外层服务才会真正开始 coding",
            "- 真正执行任务时，优先沿用当前会话上下文",
            "- 外层 GitHub App 仍负责排队、仓库准备和最终 PR 流程",
            "",
            "Issue 正文：",
            issue_body,
        ]
    )
    return "\n".join(lines).strip()


def build_feishu_discussion_prompt(
    repo_full_name: str,
    issue_number: int,
    issue_title: str,
    handoff_prompt: str,
    recent_messages: list[dict[str, Any]],
) -> str:
    transcript_lines: list[str] = []
    for message in recent_messages[-8:]:
        content = short_text(str(message.get("content") or "").strip(), 1000)
        if not content:
            continue
        sender_type = str(message.get("sender_type") or "").strip().lower()
        sender = "用户" if sender_type == "user" else "助手"
        transcript_lines.append(f"{sender}: {content}")

    lines = [
        f"你正在继续处理 GitHub Issue #{issue_number} 的飞书讨论阶段。",
        f"仓库：{repo_full_name}",
        f"标题：{issue_title}",
        "",
        "当前要求：",
        "- 现在只讨论方案、边界、改动计划和风险。",
        "- 不要开始 coding，不要假装已经执行。",
        "- 不要输出 `result:` / `summary:` / `tests:` / `risks:` 模板。",
        "- 回复直接发给飞书用户，保持简洁明确。",
        "- 如果用户还没有明确发送 `/run`，不要把讨论当成执行确认。",
        "",
        "交接背景：",
        short_text(handoff_prompt, 5000),
        "",
        "线程最近消息：",
        "\n".join(transcript_lines) if transcript_lines else "(无)",
        "",
        "请只输出你要发回飞书线程的正文。",
    ]
    return "\n".join(lines).strip()


def ensure_issue_handoff_binding(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    issue: dict[str, Any],
    handoff_prompt: str,
) -> tuple[sqlite3.Row, bool]:
    settings = resolve_feishu_runtime_settings(config)
    chat_id = settings["chat_id"]
    existing_binding = preferred_issue_binding(repo_full_name, issue_number, chat_id=chat_id)
    created_new = existing_binding is None

    if existing_binding is None:
        root_message_id = feishu_send_text_message(
            config,
            chat_id,
            build_feishu_handoff_intro(repo_full_name, issue),
        )
        prompt_message_id = feishu_reply_in_thread(config, root_message_id, handoff_prompt)
        prompt_message = feishu_get_message(config, prompt_message_id)
        thread_id = str(prompt_message.get("thread_id") or "").strip()
        if not thread_id:
            root_message = feishu_get_message(config, root_message_id)
            thread_id = str(root_message.get("thread_id") or "").strip()
        if not thread_id:
            raise RuntimeError("Feishu thread_id is missing after creating handoff thread")
        last_seen_message_id = prompt_message_id
        last_seen_message_time = str(prompt_message.get("create_time") or "")
    else:
        root_message_id = (
            str(existing_binding["root_message_id"] or "").strip()
            or str(existing_binding["prompt_message_id"] or "").strip()
        )
        if not root_message_id:
            raise RuntimeError("Feishu binding exists but root_message_id is missing")
        thread_id = str(existing_binding["thread_id"] or "").strip()
        if not thread_id:
            raise RuntimeError("Feishu binding exists but thread_id is missing")
        prompt_message_id = feishu_reply_in_thread(config, root_message_id, handoff_prompt)
        prompt_message = feishu_get_message(config, prompt_message_id)
        refreshed_thread_id = str(prompt_message.get("thread_id") or "").strip()
        if refreshed_thread_id:
            thread_id = refreshed_thread_id
        last_seen_message_id = prompt_message_id
        last_seen_message_time = str(prompt_message.get("create_time") or "")

    # 讨论阶段统一由 issue-bot 主动轮询线程并代理给 OpenClaw issue session，
    # 不再依赖 gateway 对 Feishu thread 的自动路由。
    route_session_key = build_feishu_thread_session_key(
        config,
        repo_full_name,
        issue_number,
        chat_id,
        thread_id,
    )
    binding = upsert_feishu_binding(
        config,
        chat_id=chat_id,
        thread_id=thread_id,
        repo_full_name=repo_full_name,
        issue_number=issue_number,
        session_key=route_session_key,
        note="auto handoff thread",
        binding_state="waiting_confirm",
        root_message_id=root_message_id,
        prompt_message_id=prompt_message_id,
        last_seen_message_id=last_seen_message_id,
        last_seen_message_time=last_seen_message_time,
        confirm_message_id="",
        confirm_message_time="",
    )
    return binding, created_new


def build_issue_payload_from_github(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
) -> dict[str, Any]:
    owner, repo = repo_full_name.split("/", 1)
    token = get_installation_token(config)
    issue = github_request(
        config,
        "GET",
        f"/repos/{owner}/{repo}/issues/{issue_number}",
        token=token,
    ).json()
    return {
        "action": "feishu_confirmed",
        "repository": {"full_name": repo_full_name},
        "issue": issue,
    }


def record_issue_trigger(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    repo_full_name = payload["repository"]["full_name"]
    issue = payload["issue"]
    issue_number = int(issue["number"])
    issue_title = issue.get("title") or f"Issue #{issue_number}"
    issue_state = issue.get("state") or "open"
    owner, repo = repo_full_name.split("/", 1)

    upsert_issue_record(
        repo_full_name,
        issue_number,
        issue_title,
        issue_state,
        last_reason=reason,
    )
    ensure_openclaw_issue_agent(
        CONFIG,
        repo_full_name,
        issue_number,
        openclaw_issue_workspace_dir(CONFIG, repo_full_name, issue_number),
    )
    session_row = ensure_issue_session(CONFIG, repo_full_name, issue_number, issue_title)
    local_session_key = str(session_row["session_key"])
    handoff_prompt = build_handoff_prompt(repo_full_name, issue, local_session_key)
    binding, created_new = ensure_issue_handoff_binding(
        CONFIG,
        repo_full_name,
        issue_number,
        issue,
        handoff_prompt,
    )
    session_row = upsert_issue_session(
        CONFIG,
        repo_full_name,
        issue_number,
        session_state="waiting_confirm",
        last_trigger_reason=reason,
        last_triggered_at=now_utc(),
        handoff_prompt=handoff_prompt,
        last_result_status="waiting_confirm",
    )

    token = get_installation_token(CONFIG)
    comment_issue(
        CONFIG,
        token,
        owner,
        repo,
        issue_number,
        textwrap.dedent(
            f"""
            {service_actor_name()} 已登记此 Issue，并已{"创建" if created_new else "复用"}飞书讨论线程。

            - Trigger: `{reason}`
            - Session: `{session_row['session_key']}`
            - State: `{session_row['session_state']}`
            - Feishu Chat: `{binding['chat_id']}`
            - Feishu Thread: `{binding['thread_id']}`
            - Confirm Keywords: `{", ".join(CONFIG['feishu_confirm_keywords'])}`

            请先在线程里讨论方案，确认后再在线程中发送 `/run`。
            """
        ).strip(),
    )
    return {
        "repo_full_name": repo_full_name,
        "issue_number": issue_number,
        "session_key": str(session_row["session_key"]),
        "session_state": str(session_row["session_state"]),
        "chat_id": str(binding["chat_id"]),
        "thread_id": str(binding["thread_id"]),
    }


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


def fetch_waiting_feishu_bindings() -> list[sqlite3.Row]:
    return fetchall(
        """
        SELECT *
        FROM feishu_bindings
        WHERE binding_state IN ('waiting_confirm', 'bound')
        ORDER BY updated_at ASC
        """
    )


def issue_payload_for_execution(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
) -> dict[str, Any]:
    return build_issue_payload_from_github(config, repo_full_name, issue_number)


def confirm_feishu_binding_and_queue(
    config: dict[str, Any],
    binding: sqlite3.Row,
    confirm_message: dict[str, Any],
) -> tuple[str, bool]:
    repo_full_name = str(binding["repo_full_name"])
    issue_number = int(binding["issue_number"])
    issue_payload = issue_payload_for_execution(config, repo_full_name, issue_number)
    job_id, _, created = create_job(issue_payload, f"feishu.confirm:{binding['chat_id']}:{binding['thread_id']}")
    issue = issue_payload["issue"]
    issue_title = issue.get("title") or f"Issue #{issue_number}"
    session_row = upsert_issue_session(
        config,
        repo_full_name,
        issue_number,
        session_state="queued",
        last_trigger_reason=f"feishu.confirm:{binding['chat_id']}:{binding['thread_id']}",
        last_triggered_at=now_utc(),
        summary=str(confirm_message.get("content") or "").strip() or None,
        last_result_status="queued",
    )
    upsert_feishu_binding(
        config,
        chat_id=str(binding["chat_id"]),
        thread_id=str(binding["thread_id"]),
        repo_full_name=repo_full_name,
        issue_number=issue_number,
        session_key=str(binding["session_key"]),
        note=str(binding["note"] or "") or None,
        binding_state="confirmed",
        root_message_id=str(binding["root_message_id"] or "") or None,
        prompt_message_id=str(binding["prompt_message_id"] or "") or None,
        last_seen_message_id=str(confirm_message["message_id"]),
        last_seen_message_time=str(confirm_message["create_time"]),
        confirm_message_id=str(confirm_message["message_id"]),
        confirm_message_time=str(confirm_message["create_time"]),
    )
    token = get_installation_token(config)
    owner, repo = repo_full_name.split("/", 1)
    comment_issue(
        config,
        token,
        owner,
        repo,
        issue_number,
        textwrap.dedent(
            f"""
            {service_actor_name()} 已收到飞书线程确认，开始排队执行。

            - Issue: `{repo_full_name}#{issue_number}`
            - Session: `{session_row['session_key']}`
            - Job: `{job_id}`
            - Title: `{issue_title}`
            """
        ).strip(),
    )
    if str(binding["root_message_id"] or "").strip():
        try:
            feishu_reply_in_thread(
                config,
                str(binding["root_message_id"]),
                textwrap.dedent(
                    f"""
                    已收到确认，开始执行。

                    - Issue: {repo_full_name}#{issue_number}
                    - Job: {job_id}
                    """
                ).strip(),
            )
        except Exception as exc:
            print(f"warning: failed to reply in Feishu thread for {repo_full_name}#{issue_number}: {exc}")
    return job_id, created


def reply_issue_execution_result_to_feishu(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    *,
    job_id: str,
    status: str,
    pr_url: str | None = None,
    result_summary: str | None = None,
    error_text: str | None = None,
) -> None:
    try:
        settings = resolve_feishu_runtime_settings(config)
        binding = preferred_issue_binding(repo_full_name, issue_number, chat_id=settings["chat_id"])
        if binding is None:
            print(f"warning: no Feishu binding found for {repo_full_name}#{issue_number}; skip result reply")
            return

        root_message_id = str(binding["root_message_id"] or "").strip() or str(binding["prompt_message_id"] or "").strip()
        if not root_message_id:
            print(f"warning: Feishu binding missing root_message_id for {repo_full_name}#{issue_number}; skip result reply")
            return

        if status == "succeeded":
            lines = [
                f"{service_actor_name()} 已完成执行。",
                f"- Issue: `{repo_full_name}#{issue_number}`",
                f"- Job: `{job_id}`",
            ]
            if pr_url:
                lines.append(f"- PR: `{pr_url}`")
            if result_summary:
                lines.extend(["", short_text(result_summary, 3000)])
        elif status == "no_change":
            lines = [
                f"{service_actor_name()} 已完成（无改动）。",
                f"- Issue: `{repo_full_name}#{issue_number}`",
                f"- Job: `{job_id}`",
            ]
            if result_summary:
                lines.extend(["", short_text(result_summary, 3000)])
        else:
            summary = short_text(error_text or result_summary or "(no error text)", 3000)
            lines = [
                f"{service_actor_name()} 执行失败。",
                f"- Issue: `{repo_full_name}#{issue_number}`",
                f"- Job: `{job_id}`",
                "",
                "错误摘要：",
                summary,
            ]

        feishu_reply_in_thread(config, root_message_id, "\n".join(lines).strip())
    except Exception as exc:
        print(f"warning: failed to post Feishu result reply for {repo_full_name}#{issue_number}: {exc}")


def reply_issue_discussion_to_feishu(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    *,
    binding: sqlite3.Row,
    recent_messages: list[dict[str, Any]],
) -> str | None:
    issue_row = fetchone(
        """
        SELECT issue_title
        FROM issues
        WHERE repo_full_name = ? AND issue_number = ?
        """,
        (repo_full_name, issue_number),
    )
    session_row = ensure_issue_session(
        config,
        repo_full_name,
        issue_number,
        str(issue_row["issue_title"]) if issue_row and issue_row["issue_title"] else f"Issue #{issue_number}",
    )
    handoff_prompt = str(session_row["handoff_prompt"] or "").strip()
    if not handoff_prompt:
        return None

    prompt = build_feishu_discussion_prompt(
        repo_full_name,
        issue_number,
        str(issue_row["issue_title"]) if issue_row and issue_row["issue_title"] else f"Issue #{issue_number}",
        handoff_prompt,
        recent_messages,
    )
    turn = run_openclaw_chat_turn(
        config,
        repo_full_name,
        issue_number,
        prompt,
        str(session_row["session_key"]),
    )
    response_text = str(turn["text"])
    agent_session_id = str(turn["agent_session_id"] or "").strip() or None

    root_message_id = str(binding["root_message_id"] or "").strip() or str(binding["prompt_message_id"] or "").strip()
    if not root_message_id:
        raise RuntimeError(f"Feishu binding missing root_message_id for {repo_full_name}#{issue_number}")
    feishu_reply_in_thread(config, root_message_id, response_text)
    upsert_issue_session(
        config,
        repo_full_name,
        issue_number,
        session_state="bound",
        agent_session_id=agent_session_id,
        summary=response_text,
        last_result_status="waiting_confirm",
    )
    return agent_session_id


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
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "repository": {"full_name": repo_full_name},
        "issue": issue,
    }
    if label_name is not None:
        payload["label"] = {"name": label_name}
    return payload


def repo_workspace_root(config: dict[str, Any], repo_full_name: str) -> Path:
    safe_name = repo_full_name.replace("/", "__")
    return ensure_dir(Path(config["repo_root"]) / safe_name)


def repo_issue_root(config: dict[str, Any], repo_full_name: str, issue_number: int) -> Path:
    return repo_workspace_root(config, repo_full_name) / "issues" / f"issue-{issue_number}"


def repo_checkout_dir(config: dict[str, Any], repo_full_name: str, issue_number: int) -> Path:
    return repo_issue_root(config, repo_full_name, issue_number) / "repo"


def openclaw_agent_registry_lock_path(config: dict[str, Any]) -> Path:
    return ensure_dir(Path(config["data_dir"]) / "openclaw") / "agents.lock"


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

    # 如果目标仓库没有自带 sync 脚本，就退回到通用的 upstream 同步流程。
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


def parse_executor_result(text: str) -> dict[str, str]:
    raw = (text or "").strip()
    if not raw:
        raise RuntimeError("executor returned empty final response")
    match = re.search(r"(?mi)^\s*result:\s*(succeeded|no_change|needs_human)\s*$", raw)
    if not match:
        raise RuntimeError("executor final response missing `result:` line")
    return {"status": match.group(1), "text": raw}


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


def list_openclaw_agents(config: dict[str, Any]) -> list[dict[str, Any]]:
    result = run_command(
        [config["openclaw_bin"], "agents", "list", "--json"],
        cwd=Path(config["app_home"]),
        env=build_openclaw_env(config),
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "openclaw agents list failed\n"
            f"{tail_text(result.stderr or result.stdout, 3000)}"
        )
    payload = json.loads((result.stdout or "").strip() or "[]")
    if not isinstance(payload, list):
        raise RuntimeError("openclaw agents list returned invalid payload")
    return [item for item in payload if isinstance(item, dict)]


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

    # 这里显式走 `agent --local`：
    # 1. 这样会直接使用当前进程里的 ROUTER_API_KEY / 自定义 provider 配置；
    # 2. 避开 gateway 对 provider/model override 的授权限制；
    # 3. 仍然复用同一个 agent + session_id，保证 issue 会话连续。
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
    parsed = parse_executor_result(str(turn["text"]))
    parsed["agent_id"] = str(turn["agent_id"])
    parsed["agent_session_id"] = str(turn["agent_session_id"] or session_key)
    return parsed


def run_executor(
    config: dict[str, Any],
    repo_full_name: str,
    issue_number: int,
    work_dir: Path,
    prompt: str,
    job_dir: Path,
    session_key: str,
) -> dict[str, str]:
    return run_openclaw(config, repo_full_name, issue_number, work_dir, prompt, job_dir, session_key)


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


def repo_has_running_job(repo_full_name: str) -> bool:
    row = fetchone(
        "SELECT job_id FROM jobs WHERE repo_full_name = ? AND status = 'running' LIMIT 1",
        (repo_full_name,),
    )
    return row is not None


def scan_waiting_feishu_confirmations() -> None:
    for binding in fetch_waiting_feishu_bindings():
        repo_full_name = str(binding["repo_full_name"])
        issue_number = int(binding["issue_number"])
        session_row = get_issue_session(repo_full_name, issue_number)
        if session_row is None:
            continue
        if str(session_row["session_state"] or "") not in {"waiting_confirm", "bound"}:
            continue
        if issue_has_active_job(repo_full_name, issue_number):
            continue

        thread_id = str(binding["thread_id"] or "").strip()
        if not thread_id:
            continue
        try:
            messages = feishu_list_thread_messages(CONFIG, thread_id, CONFIG["feishu_thread_scan_limit"])
        except Exception as exc:
            error_summary = short_text(str(exc), 1500)
            print(
                f"warning: failed to scan Feishu thread for "
                f"{repo_full_name}#{issue_number}: {error_summary}"
            )
            if feishu_group_message_scope_missing(exc):
                warning_marker = "warning:missing-im-message-group-msg-scope"
                note = str(binding["note"] or "")
                warning_text = (
                    "当前飞书应用缺少 `im:message.group_msg` 权限，"
                    "暂时无法读取群线程消息，因此不会响应讨论消息或 `/run`。\n\n"
                    "请在飞书开放平台为该应用开通这个权限后，再回到这个线程重试。"
                )
                root_message_id = (
                    str(binding["root_message_id"] or "").strip()
                    or str(binding["prompt_message_id"] or "").strip()
                )
                if warning_marker not in note and root_message_id:
                    try:
                        feishu_reply_in_thread(CONFIG, root_message_id, warning_text)
                    except Exception as reply_exc:
                        print(
                            f"warning: failed to post Feishu permission warning for "
                            f"{repo_full_name}#{issue_number}: {short_text(str(reply_exc), 800)}"
                        )
                if warning_marker not in note:
                    upsert_feishu_binding(
                        CONFIG,
                        chat_id=str(binding["chat_id"] or "").strip(),
                        thread_id=thread_id,
                        repo_full_name=repo_full_name,
                        issue_number=issue_number,
                        session_key=str(binding["session_key"] or "").strip(),
                        binding_state=str(binding["binding_state"] or "waiting_confirm"),
                        note=append_note_marker(note, warning_marker),
                        root_message_id=str(binding["root_message_id"] or "").strip() or None,
                        prompt_message_id=str(binding["prompt_message_id"] or "").strip() or None,
                        last_seen_message_id=str(binding["last_seen_message_id"] or "").strip() or None,
                        last_seen_message_time=str(binding["last_seen_message_time"] or "").strip() or None,
                        confirm_message_id=str(binding["confirm_message_id"] or "").strip() or None,
                        confirm_message_time=str(binding["confirm_message_time"] or "").strip() or None,
                    )
                    upsert_issue_session(
                        CONFIG,
                        repo_full_name,
                        issue_number,
                        summary=warning_text,
                        last_result_status="waiting_confirm",
                    )
            continue
        newest_seen_id = str(binding["last_seen_message_id"] or "").strip()
        newest_seen_time = str(binding["last_seen_message_time"] or "").strip()
        confirm_message: dict[str, Any] | None = None
        discussion_messages: list[dict[str, Any]] = []

        for message in messages:
            if not feishu_message_marker_is_newer(
                message,
                str(binding["last_seen_message_time"] or ""),
                str(binding["last_seen_message_id"] or ""),
            ):
                continue
            newest_seen_id = str(message["message_id"])
            newest_seen_time = str(message["create_time"])
            if str(message.get("sender_type") or "").strip().lower() != "user":
                continue
            if message_matches_confirm_keywords(str(message.get("content") or ""), CONFIG["feishu_confirm_keywords"]):
                confirm_message = message
                break
            discussion_messages.append(message)

        if discussion_messages:
            for message in discussion_messages:
                try:
                    visible_messages = []
                    for item in messages:
                        visible_messages.append(item)
                        if str(item.get("message_id") or "") == str(message.get("message_id") or ""):
                            break
                    reply_issue_discussion_to_feishu(
                        CONFIG,
                        repo_full_name,
                        issue_number,
                        binding=binding,
                        recent_messages=visible_messages,
                    )
                except Exception as exc:
                    error_summary = short_text(str(exc), 1500)
                    print(
                        f"warning: failed to proxy Feishu discussion for "
                        f"{repo_full_name}#{issue_number}: {error_summary}"
                    )
                    root_message_id = str(binding["root_message_id"] or "").strip() or str(binding["prompt_message_id"] or "").strip()
                    if root_message_id:
                        try:
                            feishu_reply_in_thread(
                                CONFIG,
                                root_message_id,
                                f"讨论阶段回复失败，请稍后重试。\n\n错误摘要：{error_summary}",
                            )
                        except Exception as reply_exc:
                            print(
                                f"warning: failed to post discussion error reply for "
                                f"{repo_full_name}#{issue_number}: {reply_exc}"
                            )

        if confirm_message is None:
            if newest_seen_id != str(binding["last_seen_message_id"] or "").strip() or newest_seen_time != str(
                binding["last_seen_message_time"] or ""
            ).strip():
                upsert_feishu_binding(
                    CONFIG,
                    chat_id=str(binding["chat_id"]),
                    thread_id=thread_id,
                    repo_full_name=repo_full_name,
                    issue_number=issue_number,
                    session_key=str(binding["session_key"]),
                    note=str(binding["note"] or "") or None,
                    binding_state=str(binding["binding_state"] or "waiting_confirm"),
                    root_message_id=str(binding["root_message_id"] or "") or None,
                    prompt_message_id=str(binding["prompt_message_id"] or "") or None,
                    last_seen_message_id=newest_seen_id or None,
                    last_seen_message_time=newest_seen_time or None,
                    confirm_message_id=str(binding["confirm_message_id"] or "") or None,
                    confirm_message_time=str(binding["confirm_message_time"] or "") or None,
                )
            continue

        job_id, created = confirm_feishu_binding_and_queue(CONFIG, binding, confirm_message)
        RUNTIME["last_queued_job_id"] = job_id
        action = "queued" if created else "reused"
        print(
            f"{action} job {job_id} for "
            f"{repo_full_name}#{issue_number} "
            f"(session={session_row['session_key']} state=queued)"
        )


def queue_payload(payload: dict[str, Any], reason: str) -> str:
    trigger_info = record_issue_trigger(payload, reason)
    action = "created" if trigger_info["session_state"] == "waiting_confirm" else "updated"
    print(
        f"{action} handoff for "
        f"{trigger_info['repo_full_name']}#{trigger_info['issue_number']} "
        f"(session={trigger_info['session_key']} state={trigger_info['session_state']})"
    )
    return str(trigger_info["session_key"])


def issue_session_payload(repo_full_name: str, issue_number: int) -> dict[str, Any]:
    # 统一给外部返回 issue / session / 飞书绑定视图，避免不同接口各自拼装字段。
    session_row = get_issue_session(repo_full_name, issue_number)
    issue_row = fetchone(
        """
        SELECT issue_title, issue_state, active_job_id, last_reason, updated_at, closed_at
        FROM issues
        WHERE repo_full_name = ? AND issue_number = ?
        """,
        (repo_full_name, issue_number),
    )
    bindings = list_issue_bindings(repo_full_name, issue_number)
    return {
        "repo_full_name": repo_full_name,
        "issue_number": issue_number,
        "issue": dict(issue_row) if issue_row else None,
        "session": dict(session_row) if session_row else None,
        "openclaw": {
            "agent_id": build_issue_agent_id(CONFIG, repo_full_name, issue_number),
            "workspace": str(openclaw_issue_workspace_dir(CONFIG, repo_full_name, issue_number)),
            "repo_path": str(repo_checkout_dir(CONFIG, repo_full_name, issue_number)),
        },
        "bindings": [dict(row) for row in bindings],
        "binding_count": len(bindings),
    }


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
            scan_waiting_feishu_confirmations()
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
    issue_root = repo_issue_root(CONFIG, repo_full_name, issue_number)
    if issue_root.exists():
        shutil.rmtree(issue_root, ignore_errors=True)
    bindings = list_issue_bindings(repo_full_name, issue_number)
    if CONFIG.get("execution_backend") == "openclaw":
        try:
            remove_openclaw_feishu_route_bindings(CONFIG, repo_full_name, issue_number, bindings=bindings)
            delete_openclaw_issue_agent(CONFIG, repo_full_name, issue_number)
        except Exception as exc:
            print(f"warning: failed to delete OpenClaw issue agent for {repo_full_name}#{issue_number}: {exc}")
    execute(
        "DELETE FROM feishu_bindings WHERE repo_full_name = ? AND issue_number = ?",
        (repo_full_name, issue_number),
    )


def handle_issue_closed(repo_full_name: str, issue: dict[str, Any]) -> None:
    issue_number = int(issue["number"])
    upsert_issue_record(
        repo_full_name,
        issue_number,
        issue.get("title") or f"Issue #{issue_number}",
        "closed",
    )
    upsert_issue_session(
        CONFIG,
        repo_full_name,
        issue_number,
        session_state="closed",
        last_result_status="closed",
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
    mark_job_running(job_id, os.getpid())

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
    active_locked = False
    repo_locked = False

    try:
        active_locked = acquire_active_lock(CONFIG, repo_full_name, issue_number)
        if not active_locked:
            raise RuntimeError(f"issue already active: {repo_full_name}#{issue_number}")

        repo_locked = acquire_repo_lock(CONFIG, repo_full_name)
        if not repo_locked:
            raise RuntimeError(f"repo lock timeout: {repo_full_name}")

        token = get_installation_token(CONFIG)
        repo_info = get_repo_info(CONFIG, token, owner, repo)
        default_branch = CONFIG["default_base_branch"] or str(repo_info.get("default_branch") or "main")

        session_row = ensure_issue_session(CONFIG, repo_full_name, issue_number, issue_title)
        session_key = str(session_row["session_key"])
        branch_name = str(session_row["branch_name"])
        _, work_dir = ensure_repo_checkout(CONFIG, repo_full_name, issue_number, default_branch, branch_name)

        prompt = build_prompt(
            repo_full_name,
            issue,
            str(work_dir),
            CONFIG["test_command"],
            session_key=session_key,
        )
        executor_result = run_executor(
            CONFIG,
            repo_full_name,
            issue_number,
            work_dir,
            prompt,
            job_dir,
            session_key,
        )
        final_status = str(executor_result["status"])
        result_summary = str(executor_result["text"])
        agent_session_id = str(executor_result.get("agent_session_id") or "").strip() or None

        upsert_issue_session(
            CONFIG,
            repo_full_name,
            issue_number,
            session_state="active",
            agent_session_id=agent_session_id,
            summary=result_summary,
            last_result_status=final_status,
        )

        if final_status == "succeeded":
            pr_title = f"{CONFIG['pr_title_prefix']} {issue_title}".strip()
            publish_result = publish_pull_request_via_git_push_and_api(
                CONFIG,
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
            upsert_issue_session(
                CONFIG,
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
            if CONFIG["submit_comment_after_pr"] and CONFIG["submit_comment_body"]:
                comment_lines.extend(["", CONFIG["submit_comment_body"]])
            comment_issue(CONFIG, token, owner, repo, issue_number, "\n".join(comment_lines).strip())
            result_summary = f"{result_summary}\n\nPR: {pr_url}"
        elif final_status == "no_change":
            upsert_issue_session(
                CONFIG,
                repo_full_name,
                issue_number,
                session_state="done",
                agent_session_id=agent_session_id,
                summary=result_summary,
                last_result_status=final_status,
            )
        else:
            upsert_issue_session(
                CONFIG,
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
        upsert_issue_session(
            CONFIG,
            repo_full_name,
            issue_number,
            session_state="failed",
            summary=error_text,
            last_result_status="failed",
        )
        raise
    finally:
        mark_job_finished(
            job_id,
            final_status,
            error_text=error_text,
            result_summary=result_summary,
        )
        clear_issue_active_job(repo_full_name, issue_number, job_id)
        if active_locked:
            release_active_lock(CONFIG, repo_full_name, issue_number)
        if repo_locked:
            release_repo_lock(CONFIG, repo_full_name)
        cleanup_closed_issue_if_finished(repo_full_name, issue_number)
        reply_issue_execution_result_to_feishu(
            CONFIG,
            repo_full_name,
            issue_number,
            job_id=job_id,
            status=final_status,
            pr_url=pr_url,
            result_summary=result_summary,
            error_text=error_text,
        )


def webhook_decision(payload: dict[str, Any], event_name: str) -> tuple[bool, str]:
    action = payload.get("action", "")
    if event_name == "issues":
        if action == "opened" and CONFIG["run_on_issue_opened"]:
            return True, "issues.opened"
        if action == "labeled":
            label = payload.get("label", {}).get("name", "")
            if label == CONFIG["trigger_label"]:
                return True, f"issues.labeled:{label}"
    return False, f"ignored:{event_name}.{action}"


def detect_poll_trigger(
    config: dict[str, Any],
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

    label_names = {label.get("name", "") for label in issue.get("labels", [])}
    if config["trigger_label"] and config["trigger_label"] in label_names:
        key = trigger_key(repo_full_name, issue_number, "label", config["trigger_label"])
        if key not in processed:
            payload = build_payload(
                repo_full_name,
                issue,
                action="labeled",
                label_name=config["trigger_label"],
            )
            return (payload, f"poll.issues_labeled:{config['trigger_label']}", key), False, False

    if config["run_on_issue_opened"]:
        key = trigger_key(repo_full_name, issue_number, "opened", "issue")
        if key not in processed:
            payload = build_payload(repo_full_name, issue, action="opened")
            return (payload, "poll.issues_opened", key), False, False

    return None, False, False


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

        if repo_needs_full_scan:
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
            "service": "coder-issue-bot",
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
            "openclaw_config_path": CONFIG.get("openclaw_config_path"),
            "openclaw_runtime_config_path": CONFIG.get("openclaw_runtime_config_path"),
            "webhook_enabled": CONFIG.get("webhook_enabled"),
            "allowed_repos": CONFIG.get("allowed_repos"),
            "execution_backend": CONFIG.get("execution_backend"),
            "backend_label": backend_label(CONFIG),
            "backend_model": backend_model_label(CONFIG),
            "run_on_issue_opened": CONFIG.get("run_on_issue_opened"),
            "trigger_label": CONFIG.get("trigger_label"),
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


@APP.get("/issues/<owner>/<repo>/<int:issue_number>/session")
def issue_session_view(owner: str, repo: str, issue_number: int) -> Any:
    repo_full_name = f"{owner}/{repo}"
    return jsonify(issue_session_payload(repo_full_name, issue_number))


@APP.post("/feishu/bind")
def feishu_bind() -> Any:
    payload = request.get_json(silent=True) or {}
    repo_full_name = str(payload.get("repo_full_name") or "").strip()
    if not repo_full_name:
        owner = str(payload.get("owner") or "").strip()
        repo = str(payload.get("repo") or "").strip()
        if owner and repo:
            repo_full_name = f"{owner}/{repo}"
    issue_number_raw = payload.get("issue_number")
    chat_id = str(payload.get("chat_id") or "").strip()
    thread_id = str(payload.get("thread_id") or "").strip()
    if not repo_full_name or issue_number_raw is None or not chat_id or not thread_id:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "repo_full_name/issue_number/chat_id/thread_id are required",
                }
            ),
            400,
        )
    if not repo_allowed(CONFIG, repo_full_name):
        return jsonify({"ok": False, "error": "repo not allowed", "repo": repo_full_name}), 403

    try:
        issue_number = int(issue_number_raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "issue_number must be an integer"}), 400
    ensure_openclaw_issue_agent(
        CONFIG,
        repo_full_name,
        issue_number,
        openclaw_issue_workspace_dir(CONFIG, repo_full_name, issue_number),
    )
    route_session_key = build_feishu_thread_session_key(
        CONFIG,
        repo_full_name,
        issue_number,
        chat_id,
        thread_id,
    )
    upsert_issue_session(
        CONFIG,
        repo_full_name,
        issue_number,
        session_state="waiting_confirm",
        last_result_status="waiting_confirm",
    )
    binding = upsert_feishu_binding(
        CONFIG,
        chat_id=chat_id,
        thread_id=thread_id,
        repo_full_name=repo_full_name,
        issue_number=issue_number,
        session_key=route_session_key,
        note=str(payload.get("note") or "").strip() or None,
        binding_state=str(payload.get("binding_state") or "waiting_confirm").strip() or "waiting_confirm",
    )
    return jsonify(
        {
            "ok": True,
            "binding": dict(binding),
            "session": issue_session_payload(repo_full_name, issue_number),
        }
    )


@APP.get("/feishu/bindings/<chat_id>/<thread_id>")
def feishu_binding_view(chat_id: str, thread_id: str) -> Any:
    binding = get_feishu_binding(chat_id, thread_id)
    if binding is None:
        return jsonify({"ok": False, "error": "binding not found"}), 404
    repo_full_name = str(binding["repo_full_name"])
    issue_number = int(binding["issue_number"])
    return jsonify(
        {
            "ok": True,
            "binding": dict(binding),
            "session": issue_session_payload(repo_full_name, issue_number),
        }
    )


@APP.delete("/feishu/bindings/<chat_id>/<thread_id>")
def feishu_binding_delete(chat_id: str, thread_id: str) -> Any:
    binding = get_feishu_binding(chat_id, thread_id)
    if binding is None:
        return jsonify({"ok": False, "error": "binding not found"}), 404
    repo_full_name = str(binding["repo_full_name"])
    issue_number = int(binding["issue_number"])
    if CONFIG.get("execution_backend") == "openclaw":
        try:
            remove_openclaw_feishu_route_bindings(CONFIG, repo_full_name, issue_number, bindings=[binding])
        except Exception as exc:
            return jsonify({"ok": False, "error": f"failed to remove OpenClaw binding: {exc}"}), 500
    deleted = delete_feishu_binding(chat_id, thread_id)
    remaining = list_issue_bindings(repo_full_name, issue_number)
    session_row = get_issue_session(repo_full_name, issue_number)
    if session_row and not remaining and str(session_row["session_state"]) in {"bound", "waiting_confirm"}:
        upsert_issue_session(
            CONFIG,
            repo_full_name,
            issue_number,
            session_state="triggered",
            last_result_status="triggered",
        )
    return jsonify(
        {
            "ok": deleted,
            "repo_full_name": repo_full_name,
            "issue_number": issue_number,
            "binding_removed": {"chat_id": chat_id, "thread_id": thread_id},
            "session": issue_session_payload(repo_full_name, issue_number),
        }
    )


@APP.post("/issues/<owner>/<repo>/<int:issue_number>/session/state")
def issue_session_state_update(owner: str, repo: str, issue_number: int) -> Any:
    payload = request.get_json(silent=True) or {}
    repo_full_name = f"{owner}/{repo}"
    if not repo_allowed(CONFIG, repo_full_name):
        return jsonify({"ok": False, "error": "repo not allowed", "repo": repo_full_name}), 403
    session_row = get_issue_session(repo_full_name, issue_number)
    if session_row is None:
        ensure_issue_session(CONFIG, repo_full_name, issue_number, f"Issue {issue_number}")
    # 这个接口用于 OpenClaw / 飞书侧回写会话进度，不参与实际 coding 执行。
    handoff_prompt = payload.get("handoff_prompt")
    summary = payload.get("summary")
    upsert_issue_session(
        CONFIG,
        repo_full_name,
        issue_number,
        session_state=str(payload.get("session_state") or "").strip() or None,
        handoff_prompt=None if handoff_prompt is None else str(handoff_prompt),
        agent_session_id=str(payload.get("agent_session_id") or "").strip() or None,
        pr_url=str(payload.get("pr_url") or "").strip() or None,
        summary=None if summary is None else str(summary),
        last_result_status=str(payload.get("last_result_status") or "").strip() or None,
    )
    return jsonify({"ok": True, "session": issue_session_payload(repo_full_name, issue_number)})


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

    trigger_id = queue_payload(payload, reason)
    return jsonify({"ok": True, "triggered": True, "trigger_id": trigger_id, "reason": reason}), 202


def default_env_file_path() -> Path:
    raw = (
        os.getenv("CODER_BOT_ENV_FILE")
        or os.getenv("CODING_BOT_ENV_FILE")
        or str(prefer_existing_path(APP_DIR / "config" / "coder-bot.env", APP_DIR / ".env"))
    )
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
    ensure_dir(CONFIG["log_dir"])
    ensure_dir(Path(CONFIG["openclaw_runtime_config_path"]).parent)
    ensure_dir(CONFIG["openclaw_state_dir"])
    ensure_openclaw_runtime_config(CONFIG)
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
    parser = argparse.ArgumentParser(description="Coder GitHub issue bot service")
    parser.add_argument(
        "--env-file",
        default=str(default_env_file_path()),
        help="Path to env file",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve")
    subparsers.add_parser("poll-once")
    subparsers.add_parser("doctor")
    subparsers.add_parser("prepare-openclaw-runtime")
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

    if args.command == "prepare-openclaw-runtime":
        ensure_dir(Path(CONFIG["openclaw_runtime_config_path"]).parent)
        ensure_dir(CONFIG["openclaw_state_dir"])
        runtime_path, _ = ensure_openclaw_runtime_config(CONFIG)
        print(runtime_path)
        return

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
