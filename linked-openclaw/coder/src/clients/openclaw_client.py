from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

from src.utils.helpers import ensure_dir, path_readable, run_command, tail_text


OPENCLAW_CONFIG_LOCK = threading.Lock()


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


def apply_openclaw_feishu_passive_mode(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if not config.get("openclaw_feishu_passive_mode", True):
        return payload

    channels = payload.get("channels")
    if not isinstance(channels, dict):
        return payload
    feishu = channels.get("feishu")
    if not isinstance(feishu, dict):
        return payload

    passive_payload = clone_json_value(payload)
    passive_channels = passive_payload.get("channels") or {}
    passive_feishu = passive_channels.get("feishu") or {}
    passive_feishu["dmPolicy"] = "allowlist"
    passive_feishu["allowFrom"] = []
    passive_feishu["groupPolicy"] = "allowlist"
    passive_feishu["groupAllowFrom"] = []
    passive_feishu["groups"] = {}
    passive_channels["feishu"] = passive_feishu
    passive_payload["channels"] = passive_channels
    return passive_payload


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
        effective_payload = apply_openclaw_feishu_passive_mode(config, effective_payload)
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
            "agents_list": desired_sections["agents_list"] if agents_explicit else current_sections["agents_list"],
            "bindings": desired_sections["bindings"] if bindings_explicit else current_sections["bindings"],
        }
        write_json_object(
            runtime_path,
            apply_openclaw_runtime_sections(cleaned_static, merged_sections),
        )


def openclaw_issue_workspace_dir(config: dict[str, Any], repo_full_name: str, issue_number: int) -> Path:
    safe_repo = repo_full_name.replace("/", "__")
    return ensure_dir(Path(config["repo_root"]) / safe_repo / "issues" / f"issue-{issue_number}")


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
        return True, f"models.providers.{provider_id}.apiKey"
    return False, f"models.providers.{provider_id}.apiKey"


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
