from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from src.clients.github_client import build_app_jwt, get_installation_token
from src.clients.openclaw_client import (
    ensure_openclaw_runtime_config,
    openclaw_provider_api_key_configured,
)
from src.db import init_db
from src.utils.helpers import (
    command_exists,
    ensure_writable_path,
    path_readable,
    run_command,
    service_actor_name,
    tail_text,
)


APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FORK_OWNER = "YeYing2025"
DEFAULT_EXECUTION_BACKEND = "openclaw"
SUPPORTED_EXECUTION_BACKENDS = {"openclaw"}
DEFAULT_GIT_AUTHOR_EMAIL = "coder-bot@local"


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs from an env file into os.environ without overriding existing values."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
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


def env_path(name: str, default: Path, *, base_dir: Path) -> str:
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
        "project_root": str(app_home),
        "gunicorn_config_path": str((app_home / "config" / "gunicorn.conf.py").resolve(strict=False)),
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
        "sync_script_abs_path": str((app_home / "scripts" / "sync.sh").resolve(strict=False)),
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
        errors.append("EXECUTION_BACKEND 只支持：" + "、".join(sorted(SUPPORTED_EXECUTION_BACKENDS)))

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
    check("GitHub App 私钥", path_readable(config["github_private_key_path"]), str(private_key_path))

    ssh_key_path = Path(config["github_clone_ssh_key_path"])
    check("Git SSH 私钥", path_readable(config["github_clone_ssh_key_path"]), str(ssh_key_path))

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

    check("OpenClaw 静态配置", path_readable(config["openclaw_config_path"]), config["openclaw_config_path"])
    if openclaw_exists and path_readable(config["openclaw_config_path"]):
        try:
            runtime_path, _ = ensure_openclaw_runtime_config(config)
            check("OpenClaw 运行时配置", True, str(runtime_path))
            from src.clients.openclaw_client import list_openclaw_agents

            agents = list_openclaw_agents(config)
            check("OpenClaw Agent Registry", True, f"{len(agents)} agents")
        except Exception as exc:
            check("OpenClaw 运行时配置", False, str(exc))
            check("OpenClaw Agent Registry", False, str(exc))
    else:
        check("OpenClaw 运行时配置", False, "skipped: static config or executable missing")
        check("OpenClaw Agent Registry", False, "skipped: config or executable missing")

    check("GitHub CLI", command_exists("gh"), "gh")
    check("Gunicorn", importlib.util.find_spec("gunicorn") is not None or command_exists("gunicorn"), "gunicorn")

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
        init_db(config)
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


def default_env_file_path() -> Path:
    raw = (
        os.getenv("CODER_BOT_ENV_FILE")
        or os.getenv("CODING_BOT_ENV_FILE")
        or str(prefer_existing_path(APP_DIR / "config" / "coder-bot.env", APP_DIR / ".env"))
    )
    return Path(raw).expanduser().resolve(strict=False)
