# Trader Runbook

## Purpose

This robot is the first resident trading-service validation inside this
repository. It is not the final production trading system.

## Modes

- `paper`: simulate fills locally and write audit records.
- `eastmoney_stub`: validate broker payloads and persist "pending execution"
  orders for manual review.

## Commands

```bash
./scripts/bootstrap.sh
./scripts/run_once.sh
./scripts/start_bot.sh
./scripts/stop_bot.sh
./scripts/status_bot.sh
```

## Runtime files

Runtime state is written under `runtime/`:

- `state.json`
- `orders.jsonl`
- `signals.jsonl`
- `logs/service.log`

## Safety

- No real broker order is sent in the current implementation.
- Credentials are loaded from `config/trader.env`.
- The service refuses to start if strategy config is missing.
