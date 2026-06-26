from __future__ import annotations

from datetime import datetime, time

from .base import MarketSnapshot, Signal, Strategy


def _parse_hhmm(value: str, fallback: str) -> time:
    raw = (value or fallback).strip()
    try:
        hour, minute = raw.split(":", 1)
        return time(hour=int(hour), minute=int(minute))
    except Exception:
        hour, minute = fallback.split(":", 1)
        return time(hour=int(hour), minute=int(minute))


def _detect_phase(now: datetime, afternoon_exit_at: time) -> str:
    current = now.time()
    if current < time(9, 29):
        return "pre_open"
    if current < time(9, 30):
        return "auction_lock"
    if current < time(9, 35):
        return "auction_open"
    if current < afternoon_exit_at:
        return "mid_session"
    return "afternoon_exit"


def _recent_returns(series: list[float]) -> list[float]:
    values: list[float] = []
    for prev, cur in zip(series, series[1:]):
        if prev:
            values.append((cur - prev) / prev)
    return values


def _round_lot(quantity: int, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    if lot_size <= 1:
        return quantity
    return max(lot_size, (quantity // lot_size) * lot_size)


def _position_state(state: dict, config: dict, observed_at: datetime) -> tuple[dict, int]:
    next_state = dict(state or {})
    configured_position = int(config.get("position_quantity", 0) or 0)
    if "remaining_quantity" not in next_state:
        next_state["remaining_quantity"] = configured_position
    if "initial_quantity" not in next_state:
        next_state["initial_quantity"] = next_state["remaining_quantity"] or configured_position
    next_state.setdefault("position_opened", next_state.get("remaining_quantity", 0) > 0)
    next_state.setdefault("morning_half_sold", False)
    next_state.setdefault("afternoon_exit_done", False)
    next_state.setdefault("entry_day", observed_at.strftime("%Y-%m-%d"))
    return next_state, int(next_state.get("remaining_quantity", 0) or 0)


def _close_position(state: dict) -> None:
    state["remaining_quantity"] = 0
    state["position_opened"] = False
    state["afternoon_exit_done"] = True


class AuctionWaveStrategy(Strategy):
    def evaluate(self, snapshot: MarketSnapshot, prior_state: dict | None = None) -> Signal:
        state = dict(prior_state or {})
        latest = snapshot.latest_price
        pre_close = snapshot.pre_close
        limit_up_pct = float(self.config.get("limit_up_threshold_pct", 9.8))
        afternoon_exit_at = _parse_hhmm(str(self.config.get("afternoon_exit_time", "14:30")), "14:30")
        enable_buy = bool(self.config.get("enable_buy", False))
        observed_at = snapshot.observed_at or datetime.now()
        phase = _detect_phase(observed_at, afternoon_exit_at)
        active_lookback = int(self.config.get("active_lookback_days", 10))
        active_threshold_pct = float(self.config.get("active_threshold_pct", 4.0))

        metadata = {
            "strategy": "auction_wave",
            "observedAt": observed_at.isoformat(timespec="seconds"),
            "phase": phase,
        }

        if latest is None or pre_close in (None, 0):
            metadata["next_state"] = state
            return Signal("hold", "missing latest or pre-close price", 0, latest, metadata)

        state, position_quantity = _position_state(state, self.config, observed_at)
        metadata["remaining_quantity"] = position_quantity
        pct = ((latest - pre_close) / pre_close) * 100.0
        metadata["change_pct"] = round(pct, 4)
        metadata["pre_close"] = pre_close

        if position_quantity > 0:
            auction_bucket = state.get("auction_bucket")
            if auction_bucket is None and phase != "pre_open":
                if pct < 0:
                    auction_bucket = "below_zero"
                elif pct <= 3:
                    auction_bucket = "zero_to_three"
                else:
                    auction_bucket = "above_three"
                state["auction_bucket"] = auction_bucket
                state["auction_bucket_locked_at"] = observed_at.isoformat(timespec="seconds")
                state["auction_bucket_source"] = "first_observed_after_09_29"

            is_limit_up = pct >= limit_up_pct
            metadata["auction_bucket"] = auction_bucket
            metadata["is_limit_up"] = is_limit_up
            metadata["position_state"] = {
                "remaining_quantity": state.get("remaining_quantity", 0),
                "initial_quantity": state.get("initial_quantity", 0),
                "morning_half_sold": bool(state.get("morning_half_sold")),
                "position_opened": bool(state.get("position_opened")),
                "entry_day": state.get("entry_day"),
                "auction_bucket_locked_at": state.get("auction_bucket_locked_at"),
            }

            if auction_bucket is None:
                metadata["next_state"] = state
                return Signal(
                    "hold",
                    "waiting for 09:29+ auction observation to lock the opening bucket",
                    0,
                    latest,
                    metadata,
                )

            if auction_bucket == "below_zero":
                _close_position(state)
                metadata["next_state"] = state
                return Signal(
                    "sell",
                    f"auction bucket below zero, exit full position at {pct:.2f}%",
                    position_quantity,
                    latest,
                    metadata,
                )

            if auction_bucket == "zero_to_three":
                lot_size = int(self.config.get("lot_size", 100) or 100)
                base_quantity = int(state.get("initial_quantity", position_quantity) or position_quantity)
                half_qty = _round_lot(base_quantity // 2, lot_size) if base_quantity > 1 else base_quantity
                half_qty = min(position_quantity, half_qty or position_quantity)
                if phase in {"auction_lock", "auction_open"} and not state.get("morning_half_sold"):
                    state["morning_half_sold"] = True
                    state["remaining_quantity"] = max(0, position_quantity - half_qty)
                    state["position_opened"] = state["remaining_quantity"] > 0
                    metadata["next_state"] = state
                    return Signal(
                        "sell",
                        f"auction in 0%-3%, sell half on opening handling step, current {pct:.2f}%",
                        half_qty,
                        latest,
                        metadata,
                    )
                if phase in {"auction_open", "mid_session", "afternoon_exit"} and pct <= 0 and position_quantity > 0:
                    _close_position(state)
                    metadata["next_state"] = state
                    return Signal(
                        "sell",
                        f"remaining half exits after price retraced to 0% or below, current {pct:.2f}%",
                        position_quantity,
                        latest,
                        metadata,
                    )
                if phase == "afternoon_exit" and not is_limit_up and position_quantity > 0 and not state.get("afternoon_exit_done"):
                    _close_position(state)
                    metadata["next_state"] = state
                    return Signal(
                        "sell",
                        f"afternoon forced exit at {afternoon_exit_at.strftime('%H:%M')} because not limit-up",
                        position_quantity,
                        latest,
                        metadata,
                )

            if auction_bucket == "above_three":
                if phase in {"auction_open", "mid_session", "afternoon_exit"} and pct < 3 and position_quantity > 0:
                    _close_position(state)
                    metadata["next_state"] = state
                    return Signal(
                        "sell",
                        f"auction >3% but intraday retraced below 3%, exit full position at {pct:.2f}%",
                        position_quantity,
                        latest,
                        metadata,
                    )
                if phase == "afternoon_exit" and not is_limit_up and position_quantity > 0 and not state.get("afternoon_exit_done"):
                    _close_position(state)
                    metadata["next_state"] = state
                    return Signal(
                        "sell",
                        f"afternoon forced exit at {afternoon_exit_at.strftime('%H:%M')} because not limit-up",
                        position_quantity,
                        latest,
                        metadata,
                    )

            metadata["next_state"] = state
            return Signal(
                "hold",
                f"holding position under {auction_bucket} rule, current {pct:.2f}%",
                0,
                latest,
                metadata,
            )

        # Buy side is intentionally approximate in v2. It adds activity and momentum
        # gates without pretending we already have full wave/board/ask-depth data.
        if enable_buy and len(snapshot.close_series) >= active_lookback:
            recent_window = snapshot.close_series[-active_lookback:]
            recent_high = max(recent_window)
            recent_low = min(recent_window)
            recent_returns = _recent_returns(recent_window)
            active_pct = (recent_high - recent_low) / recent_low * 100 if recent_low else 0
            avg_return_pct = (sum(abs(v) for v in recent_returns) / len(recent_returns) * 100) if recent_returns else 0
            resistance_lookback = int(self.config.get("resistance_lookback_days", max(active_lookback * 2, 20)))
            resistance_buffer_pct = float(self.config.get("resistance_buffer_pct", 1.0))
            resistance_window = snapshot.close_series[-resistance_lookback:] if len(snapshot.close_series) >= resistance_lookback else snapshot.close_series
            resistance_high = max(resistance_window) if resistance_window else recent_high
            breakout_buffer_pct = float(self.config.get("breakout_buffer_pct", 0.2))
            breakout_trigger = recent_high * (1 + breakout_buffer_pct / 100.0)
            not_near_resistance = latest >= resistance_high or latest <= resistance_high * (1 - resistance_buffer_pct / 100.0)
            metadata["buy_gate"] = {
                "active_pct": round(active_pct, 4),
                "avg_return_pct": round(avg_return_pct, 4),
                "recent_high": recent_high,
                "recent_low": recent_low,
                "lookback_days": active_lookback,
                "resistance_high": resistance_high,
                "resistance_lookback_days": resistance_lookback,
                "resistance_buffer_pct": resistance_buffer_pct,
                "breakout_trigger": round(breakout_trigger, 4),
                "breakout_buffer_pct": breakout_buffer_pct,
            }
            is_active = active_pct >= active_threshold_pct or avg_return_pct >= (active_threshold_pct / 2)
            near_breakout = latest >= breakout_trigger
            if phase in {"auction_open", "mid_session"} and is_active and near_breakout and not_near_resistance:
                buy_qty = int(self.config.get("quantity", 100))
                state["remaining_quantity"] = buy_qty
                state["initial_quantity"] = buy_qty
                state["auction_bucket"] = None
                state["morning_half_sold"] = False
                state["afternoon_exit_done"] = False
                state["position_opened"] = True
                state["entry_day"] = observed_at.strftime("%Y-%m-%d")
                state.pop("auction_bucket_locked_at", None)
                state.pop("auction_bucket_source", None)
                metadata["next_state"] = state
                metadata["buy_mode"] = "activity_plus_breakout_gate"
                return Signal(
                    "buy",
                    "buy-side approximate gate passed: active recent window, breakout confirmed, no strong resistance",
                    buy_qty,
                    latest,
                    metadata,
                )

        metadata["next_state"] = state
        return Signal(
            "hold",
            "no sell trigger and buy-side approximation did not pass",
            0,
            latest,
            metadata,
        )
