from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .brokers.eastmoney_stub import EastMoneyStubBroker
from .brokers.paper import PaperBroker
from .config import AppConfig, load_app_config, load_strategy_config
from .data.ifind_client import IfindClient, IfindSettings
from .execution.runner import run_cycle
from .scheduler import run_forever
from .state_store import StateStore


def build_logger(runtime_dir: Path, level: str) -> logging.Logger:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    log_file = runtime_dir / "logs" / "service.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("trader-bot")
    logger.handlers.clear()
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def build_broker(config: AppConfig):
    if config.broker == "eastmoney_stub":
        return EastMoneyStubBroker(config.eastmoney_account_id)
    return PaperBroker()


def run_once(config: AppConfig) -> dict:
    logger = build_logger(config.runtime_dir, config.log_level)
    if not config.strategy_file.exists():
        raise FileNotFoundError(f"strategy file not found: {config.strategy_file}")
    strategies = load_strategy_config(config.strategy_file)
    ifind_client = IfindClient(
        IfindSettings(
            base_url=config.ifind_base_url,
            access_token=config.ifind_access_token,
            refresh_token=config.ifind_refresh_token,
            timeout_ms=config.ifind_request_timeout_ms,
        )
    )
    state_store = StateStore(config.runtime_dir)
    broker = build_broker(config)
    result = run_cycle(
        strategies=strategies,
        ifind_client=ifind_client,
        broker=broker,
        runtime_dir=config.runtime_dir,
        state_store=state_store,
        logger=logger,
    )
    logger.info("cycle result=%s", json.dumps(result, ensure_ascii=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run-once", "run-loop"], nargs="?", default="run-once")
    parser.add_argument("--env-file", dest="env_file")
    args = parser.parse_args()

    app_dir = Path(__file__).resolve().parent.parent
    env_file = Path(args.env_file).resolve() if args.env_file else None
    config = load_app_config(app_dir, env_file)

    if args.command == "run-once":
        run_once(config)
        return

    logger = build_logger(config.runtime_dir, config.log_level)

    def cycle() -> None:
        run_once(config)

    run_forever(config.loop_interval_seconds, cycle, logger)


if __name__ == "__main__":
    main()
