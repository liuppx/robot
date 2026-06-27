from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from hub.adapters.trader import TraderAdapter


class TraderAdapterTest(unittest.TestCase):
    def build_root(self, root: Path) -> Path:
        trader_root = root / "robots" / "custom" / "trader"
        (trader_root / "config").mkdir(parents=True, exist_ok=True)
        return trader_root

    def write_strategy_file(self, trader_root: Path) -> Path:
        strategy_path = trader_root / "config" / "strategies.yaml"
        strategy_path.write_text(
            yaml.safe_dump(
                {
                    "strategies": [
                        {
                            "id": "etf-breakout-demo",
                            "symbol": "510300.SH",
                            "name": "CSI300 ETF Demo",
                            "strategy": "breakout",
                            "enabled": True,
                        },
                        {
                            "id": "auction-wave-demo",
                            "symbol": "159915.SZ",
                            "name": "Auction Wave Demo",
                            "strategy": "auction_wave",
                            "enabled": False,
                        },
                    ]
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return strategy_path

    def write_runtime_files(self, trader_root: Path) -> None:
        runtime_dir = trader_root / "runtime" / "logs"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (trader_root / "runtime" / "state.json").write_text(
            '{"strategies":{"etf-breakout-demo":{"symbol":"510300.SH","strategy":"breakout","lastAction":"hold","lastReason":"inside range","latestPrice":4.907,"observedAt":"2026-06-27T16:01:24","riskOk":true,"riskReason":"no order required","snapshotPath":"/tmp/snapshot.jsonl","strategyState":{},"metadata":{},"orderResult":null}}}',
            encoding="utf-8",
        )
        (trader_root / "runtime" / "signals.jsonl").write_text(
            '{"ts":"2026-06-27T08:29:59.616885+00:00","strategyId":"etf-breakout-demo","symbol":"510300.SH","action":"hold"}\n',
            encoding="utf-8",
        )
        (trader_root / "runtime" / "orders.jsonl").write_text("", encoding="utf-8")
        (trader_root / "runtime" / "logs" / "service.log").write_text(
            '2026-06-27 16:29:59,618 INFO cycle result={"brokerStatus":{"provider":"paper"},"strategyCount":1,"results":[{"symbol":"510300.SH"}]}\n',
            encoding="utf-8",
        )

    def test_update_config_preserves_other_strategies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            trader_root = self.build_root(Path(tmpdir))
            strategy_path = self.write_strategy_file(trader_root)
            adapter = TraderAdapter(trader_root)

            result = adapter.update_config(
                "paper",
                {
                    "id": "auction-wave-demo",
                    "symbol": "510300.SH",
                    "name": "Auction Wave Demo Updated",
                    "strategy": "auction_wave",
                    "enabled": True,
                },
                "auction-wave-demo",
            )

            self.assertTrue(result.saved)
            self.assertEqual(result.strategyCount, 2)
            loaded = yaml.safe_load(strategy_path.read_text(encoding="utf-8"))
            strategies = loaded["strategies"]
            self.assertEqual(len(strategies), 2)
            self.assertEqual(strategies[0]["id"], "etf-breakout-demo")
            self.assertEqual(strategies[1]["id"], "auction-wave-demo")
            self.assertEqual(strategies[1]["name"], "Auction Wave Demo Updated")
            self.assertTrue(strategies[1]["enabled"])

    def test_update_config_can_match_old_strategy_id_when_renaming(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            trader_root = self.build_root(Path(tmpdir))
            strategy_path = self.write_strategy_file(trader_root)
            adapter = TraderAdapter(trader_root)

            result = adapter.update_config(
                "eastmoney_stub",
                {
                    "id": "auction-wave-live",
                    "symbol": "159915.SZ",
                    "name": "Auction Wave Live",
                    "strategy": "auction_wave",
                    "enabled": True,
                },
                "auction-wave-demo",
            )

            self.assertTrue(result.saved)
            self.assertEqual(result.broker, "eastmoney_stub")
            loaded = yaml.safe_load(strategy_path.read_text(encoding="utf-8"))
            strategy_ids = [item["id"] for item in loaded["strategies"]]
            self.assertEqual(strategy_ids, ["etf-breakout-demo", "auction-wave-live"])

    def test_summary_exposes_last_cycle_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            trader_root = self.build_root(Path(tmpdir))
            self.write_strategy_file(trader_root)
            self.write_runtime_files(trader_root)
            adapter = TraderAdapter(trader_root)

            summary = adapter.summary()

            self.assertEqual(summary.last_cycle_strategy_count, 1)
            self.assertEqual(summary.last_cycle_request_count, 2)
            self.assertEqual(summary.last_snapshot_path, "/tmp/snapshot.jsonl")


if __name__ == "__main__":
    unittest.main()
