from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_env_file(path: Path) -> None:
    for key, value in _parse_env_file(path).items():
        os.environ.setdefault(key, value)


@dataclass
class AppConfig:
    app_dir: Path
    env_file: Path
    runtime_dir: Path
    strategy_file: Path
    loop_interval_seconds: int
    log_level: str
    broker: str
    ifind_base_url: str
    ifind_access_token: str | None
    ifind_refresh_token: str | None
    ifind_request_timeout_ms: int
    eastmoney_account_id: str | None


def load_strategy_config(path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    strategies = payload.get("strategies") or []
    if not isinstance(strategies, list):
        raise ValueError("strategies must be a list")
    return strategies


def load_app_config(app_dir: Path, env_file: Path | None = None) -> AppConfig:
    env_file = env_file or app_dir / "config" / "trader.env"
    load_env_file(env_file)

    runtime_dir = Path(os.environ.get("TRADER_RUNTIME_DIR", "runtime"))
    if not runtime_dir.is_absolute():
        runtime_dir = app_dir / runtime_dir

    strategy_file = Path(os.environ.get("TRADER_STRATEGY_FILE", "config/strategies.yaml"))
    if not strategy_file.is_absolute():
        strategy_file = app_dir / strategy_file

    return AppConfig(
        app_dir=app_dir,
        env_file=env_file,
        runtime_dir=runtime_dir,
        strategy_file=strategy_file,
        loop_interval_seconds=int(os.environ.get("TRADER_LOOP_INTERVAL_SECONDS", "300")),
        log_level=os.environ.get("TRADER_LOG_LEVEL", "INFO"),
        broker=os.environ.get("TRADER_BROKER", "paper").strip().lower(),
        ifind_base_url=os.environ.get("IFIND_BASE_URL", "https://quantapi.51ifind.com/api/v1").rstrip("/"),
        ifind_access_token=os.environ.get("IFIND_ACCESS_TOKEN") or None,
        ifind_refresh_token=os.environ.get("IFIND_REFRESH_TOKEN") or None,
        ifind_request_timeout_ms=int(os.environ.get("IFIND_REQUEST_TIMEOUT_MS", "30000")),
        eastmoney_account_id=os.environ.get("TRADER_EASTMONEY_ACCOUNT_ID") or None,
    )


def dump_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
