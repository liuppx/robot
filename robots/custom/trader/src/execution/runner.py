from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..audit.logger import append_jsonl
from ..brokers.base import BrokerAdapter, OrderIntent
from ..data.ifind_client import (
    IfindClient,
    extract_close_series,
    extract_latest_price,
    extract_observed_at,
    extract_pre_close,
)
from ..risk.rules import evaluate_risk
from ..state_store import StateStore
from ..strategies.base import MarketSnapshot
from ..strategies.auction_wave import AuctionWaveStrategy
from ..strategies.breakout import BreakoutStrategy
from .planner import build_order_intent


def _snapshot_file(runtime_dir: Path, run_day: date, strategy_id: str, symbol: str) -> Path:
    safe_strategy = strategy_id.replace("/", "_").replace(":", "_").replace(" ", "_")
    safe_symbol = symbol.replace("/", "_").replace(":", "_").replace(" ", "_")
    return runtime_dir / "snapshots" / run_day.isoformat() / f"{safe_strategy}__{safe_symbol}.jsonl"


def _build_strategy(strategy_config: dict[str, Any]):
    strategy_type = str(strategy_config.get("strategy", "breakout")).strip().lower()
    if strategy_type == "auction_wave":
        return AuctionWaveStrategy(strategy_config)
    return BreakoutStrategy(strategy_config)


def run_cycle(
    *,
    strategies: list[dict[str, Any]],
    ifind_client: IfindClient,
    broker: BrokerAdapter,
    runtime_dir,
    state_store: StateStore,
    logger: logging.Logger,
) -> dict[str, Any]:
    signals_path = runtime_dir / "signals.jsonl"
    orders_path = runtime_dir / "orders.jsonl"
    today = date.today()
    startdate = str(today - timedelta(days=40))
    enddate = str(today)
    current_state = state_store.load()
    current_state.setdefault("strategies", {})
    results: list[dict[str, Any]] = []

    for strategy_config in strategies:
        if not strategy_config.get("enabled", True):
            continue
        symbol = strategy_config["symbol"]
        logger.info("evaluating strategy=%s symbol=%s", strategy_config["id"], symbol)
        realtime_payload = ifind_client.query_realtime_quotes(symbol)
        history_payload = ifind_client.query_history_quotes(symbol, startdate=startdate, enddate=enddate)
        snapshot_path = _snapshot_file(runtime_dir, today, strategy_config["id"], symbol)
        append_jsonl(
            snapshot_path,
            {
                "strategyId": strategy_config["id"],
                "strategy": strategy_config.get("strategy", "breakout"),
                "symbol": symbol,
                "requestWindow": {
                    "startdate": startdate,
                    "enddate": enddate,
                },
                "realtimePayload": realtime_payload,
                "historyPayload": history_payload,
            },
        )
        snapshot = MarketSnapshot(
            symbol=symbol,
            latest_price=extract_latest_price(realtime_payload),
            pre_close=extract_pre_close(realtime_payload),
            close_series=extract_close_series(history_payload),
            observed_at=extract_observed_at(realtime_payload),
        )
        strategy = _build_strategy(strategy_config)
        previous_entry = current_state["strategies"].get(strategy_config["id"], {})
        previous_state = previous_entry.get("strategyState", previous_entry)
        signal = strategy.evaluate(snapshot, previous_state)
        risk_ok, risk_reason = evaluate_risk(strategy_config, signal.action, signal.target_quantity)
        signal_record = {
            "strategyId": strategy_config["id"],
            "symbol": symbol,
            "action": signal.action,
            "reason": signal.reason,
            "latestPrice": signal.latest_price,
            "riskOk": risk_ok,
            "riskReason": risk_reason,
            "metadata": signal.metadata,
        }
        append_jsonl(signals_path, signal_record)

        order_result = None
        intent = build_order_intent(strategy_config, signal)
        if intent and risk_ok:
            dry_run_result = broker.dry_run_order(intent)
            if intent.dry_run:
                order_result = {
                    "mode": "dry_run",
                    "dryRun": dry_run_result,
                }
            else:
                order_result = {
                    "mode": "execute",
                    "dryRun": dry_run_result,
                    "order": broker.place_order(intent),
                }
            append_jsonl(
                orders_path,
                {
                    "strategyId": strategy_config["id"],
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "quantity": intent.quantity,
                    "latestPrice": intent.latest_price,
                    "result": order_result,
                },
            )

        next_state = signal.metadata.get("next_state", previous_state)
        current_state["strategies"][strategy_config["id"]] = {
            "symbol": symbol,
            "strategy": strategy_config.get("strategy", "breakout"),
            "lastAction": signal.action,
            "lastReason": signal.reason,
            "latestPrice": signal.latest_price,
            "preClose": snapshot.pre_close,
            "observedAt": snapshot.observed_at.isoformat(timespec="seconds") if snapshot.observed_at else None,
            "riskOk": risk_ok,
            "riskReason": risk_reason,
            "snapshotPath": str(snapshot_path),
            "strategyState": next_state,
            "metadata": signal.metadata,
            "orderResult": order_result,
        }
        results.append(current_state["strategies"][strategy_config["id"]])

    state_store.save(current_state)
    return {
        "brokerStatus": broker.broker_status(),
        "strategyCount": len(results),
        "results": results,
    }
