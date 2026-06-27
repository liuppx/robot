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


if __name__ == "__main__":
    unittest.main()
