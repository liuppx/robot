from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

from control_plane.models import (
    TraderActionResponse,
    TraderConfigUpdateResponse,
    TraderSummaryResponse,
)


class TraderAdapter:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def exists(self) -> bool:
        return self.root_dir.exists()

    @property
    def env_file(self) -> Path:
        return self.root_dir / "config" / "trader.env"

    @property
    def env_template(self) -> Path:
        return self.root_dir / "config" / "trader.env.template"

    def read_env_values(self) -> dict[str, str]:
        source = self.env_file if self.env_file.exists() else self.env_template
        values: dict[str, str] = {}
        if not source.exists():
            return values
        for line in source.read_text(encoding="utf-8").splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
                continue
            key, value = trimmed.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        return values

    def write_env_values(self, values: dict[str, str]) -> None:
        env_order = [
            "TRADER_BOT_NAME",
            "TRADER_BIND_MODE",
            "TRADER_LOOP_INTERVAL_SECONDS",
            "TRADER_LOG_LEVEL",
            "TRADER_RUNTIME_DIR",
            "TRADER_STRATEGY_FILE",
            "TRADER_BROKER",
            "IFIND_BASE_URL",
            "IFIND_ACCESS_TOKEN",
            "IFIND_REFRESH_TOKEN",
            "IFIND_REQUEST_TIMEOUT_MS",
            "TRADER_EASTMONEY_ACCOUNT_ID",
        ]
        lines: list[str] = []
        for key in env_order:
            if key in values:
                lines.append(f"{key}={values[key]}")
        for key, value in values.items():
            if key in env_order:
                continue
            lines.append(f"{key}={value}")
        self.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def runtime_dir(self, env_values: dict[str, str]) -> Path:
        runtime_dir = env_values.get("TRADER_RUNTIME_DIR", "runtime")
        runtime_path = Path(runtime_dir)
        return runtime_path if runtime_path.is_absolute() else self.root_dir / runtime_path

    def strategy_file(self, env_values: dict[str, str]) -> Path:
        strategy_file = env_values.get("TRADER_STRATEGY_FILE", "config/strategies.yaml")
        strategy_path = Path(strategy_file)
        return strategy_path if strategy_path.is_absolute() else self.root_dir / strategy_path

    @staticmethod
    def read_json_file_or_default(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in [line for line in lines if line.strip()][-limit:]:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    @staticmethod
    def read_text_tail(path: Path, limit: int) -> str:
        if not path.exists():
            return ""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-limit:])

    @staticmethod
    def read_trader_strategies(path: Path) -> list[Any]:
        if not path.exists():
            return []
        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return []
        if not isinstance(parsed, dict):
            return []
        strategies = parsed.get("strategies", [])
        return strategies if isinstance(strategies, list) else []

    @staticmethod
    def trader_pid(runtime_dir: Path) -> int | None:
        pid_file = runtime_dir / "trader.pid"
        if not pid_file.exists():
            return None
        try:
            return int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def is_process_running(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def summary(self) -> TraderSummaryResponse:
        missing_runtime = self.root_dir / "runtime"
        missing_strategy = self.root_dir / "config" / "strategies.yaml"
        missing_log = missing_runtime / "logs" / "service.log"
        missing_state = missing_runtime / "state.json"
        if not self.exists():
            return TraderSummaryResponse(
                available=False,
                broker="unavailable",
                running=False,
                pid=None,
                runtime_dir=str(missing_runtime),
                strategy_file=str(missing_strategy),
                state_file=str(missing_state),
                service_log_path=str(missing_log),
                strategies=[],
                state={},
                recent_signals=[],
                recent_orders=[],
                service_log_tail="",
            )

        env_values = self.read_env_values()
        broker = env_values.get("TRADER_BROKER", "paper")
        runtime_dir = self.runtime_dir(env_values)
        strategy_file = self.strategy_file(env_values)
        pid = self.trader_pid(runtime_dir)
        running = pid is not None and self.is_process_running(pid)

        state_file = runtime_dir / "state.json"
        signals_file = runtime_dir / "signals.jsonl"
        orders_file = runtime_dir / "orders.jsonl"
        service_log_path = runtime_dir / "logs" / "service.log"

        return TraderSummaryResponse(
            available=True,
            broker=broker,
            running=running,
            pid=pid if running else None,
            runtime_dir=str(runtime_dir),
            strategy_file=str(strategy_file),
            state_file=str(state_file),
            service_log_path=str(service_log_path),
            strategies=self.read_trader_strategies(strategy_file),
            state=self.read_json_file_or_default(state_file),
            recent_signals=self.read_jsonl_tail(signals_file, 8),
            recent_orders=self.read_jsonl_tail(orders_file, 8),
            service_log_tail=self.read_text_tail(service_log_path, 40),
        )

    def update_config(self, broker: str, strategy: dict[str, Any]) -> TraderConfigUpdateResponse:
        normalized_broker = broker.strip().lower()
        if normalized_broker not in {"paper", "eastmoney_stub"}:
            raise ValueError("trader broker must be paper or eastmoney_stub")

        strategy_id = str(strategy.get("id", "")).strip()
        symbol = str(strategy.get("symbol", "")).strip()
        if not strategy_id or not symbol:
            raise ValueError("strategy id and symbol are required")

        env_values = self.read_env_values()
        env_values["TRADER_BROKER"] = normalized_broker
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.write_env_values(env_values)

        strategy_path = self.strategy_file(env_values)
        strategy_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"strategies": [strategy]}
        strategy_path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        return TraderConfigUpdateResponse(
            saved=True,
            broker=normalized_broker,
            strategyCount=1,
        )

    def run_action(self, action: str) -> TraderActionResponse:
        script_name_by_action = {
            "run_once": "run_once.sh",
            "start": "start_bot.sh",
            "stop": "stop_bot.sh",
        }
        script_name = script_name_by_action.get(action)
        if script_name is None:
            raise ValueError(f"unsupported trader action: {action}")

        script_path = (self.root_dir / "scripts" / script_name).resolve()
        if not script_path.exists():
            raise FileNotFoundError(f"missing trader script: {script_path}")

        try:
            completed = subprocess.run(
                [str(script_path)],
                cwd=self.root_dir,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            message = (exc.stdout or "").strip()
            stderr = (exc.stderr or "").strip()
            if stderr:
                message = f"{message}\n{stderr}".strip() if message else stderr
            raise OSError(message or f"trader action failed: {action}") from exc

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if stderr:
            stdout = f"{stdout}\n{stderr}".strip() if stdout else stderr
        return TraderActionResponse(
            executed=True,
            action=action,
            stdout=stdout,
        )
