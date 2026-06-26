from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from ..audit.logger import append_jsonl
from ..brokers.base import BrokerAdapter, OrderIntent
from ..data.ifind_client import IfindClient, extract_close_series, extract_latest_price
from ..risk.rules import evaluate_risk
from ..state_store import StateStore
from ..strategies.base import MarketSnapshot
from ..strategies.breakout import BreakoutStrategy
from .planner import build_order_intent


def _build_strategy(strategy_config: dict[str, Any]) -> BreakoutStrategy:
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
        snapshot = MarketSnapshot(
            symbol=symbol,
            latest_price=extract_latest_price(realtime_payload),
            close_series=extract_close_series(history_payload),
        )
        strategy = _build_strategy(strategy_config)
        signal = strategy.evaluate(snapshot)
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

        current_state["strategies"][strategy_config["id"]] = {
            "symbol": symbol,
            "lastAction": signal.action,
            "lastReason": signal.reason,
            "latestPrice": signal.latest_price,
            "riskOk": risk_ok,
            "riskReason": risk_reason,
            "orderResult": order_result,
        }
        results.append(current_state["strategies"][strategy_config["id"]])

    state_store.save(current_state)
    return {
        "brokerStatus": broker.broker_status(),
        "strategyCount": len(results),
        "results": results,
    }
