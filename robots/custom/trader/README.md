# Trader

`robots/custom/trader` is a resident strategy-trading robot used to validate the full
robot workflow:

1. fetch TongHuaShun iFinD market data,
2. compute strategy signals,
3. apply risk gates,
4. generate order intents,
5. route intents through a broker adapter,
6. persist audit logs and service state.

The first version is intentionally conservative:

- market data uses real iFinD / QuantAPI credentials from local env;
- execution defaults to `paper`;
- `eastmoney_stub` validates the broker boundary without sending real orders;
- no real broker order is sent from this repository yet.

## Layout

```text
robots/custom/trader/
  config/
  docs/
  scripts/
  src/
```

## Quick start

```bash
cd robots/custom/trader
./scripts/bootstrap.sh
./scripts/run_once.sh
./scripts/start_bot.sh
./scripts/status_bot.sh
```

Local credentials live in `config/trader.env`, which is ignored by git.
