"""Multi-asset paper broker regression.

Seeds a throwaway simulation bot, exercises equity/option/crypto orders, resting
limit orders, cancels, duplicate intents, and risk blocks, then asserts the
resulting cash, positions, and realized P&L. Run: python scripts/sim_regression.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.simulation import positions as pos
from src.simulation.engine import OrderIntent, PaperBroker, empty_ledger
from src.simulation.migration import upgrade_ledger

FAILURES: list[str] = []


def check(label: str, actual, expected, tol: float = 1e-6) -> None:
    ok = (
        abs(actual - expected) <= tol
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float))
        else actual == expected
    )
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {actual!r}" + ("" if ok else f" (expected {expected!r})"))
    if not ok:
        FAILURES.append(label)


class StubBroker(PaperBroker):
    """PaperBroker with in-memory settings, quotes, and no database."""

    def __init__(self, prices: dict[str, float], limits, cash: float = 10_000.0, fill_model=None) -> None:
        self.bot_id = "regression"
        self.ledger = empty_ledger()
        self.ledger.update({"cash": cash, "settled_cash": cash, "seeded": True})
        self.prices = prices
        from src.simulation.fills import FillModel

        self.fill_model = fill_model or FillModel()
        self._limits = limits

    def save(self):
        return self.ledger

    def _quote_price(self, symbol, asset_class, quotes):
        return self.prices.get(str(symbol).upper())


class Limits:
    max_open_positions = 5
    max_daily_loss_usd = 1_000.0
    max_order_notional_usd = 5_000.0


def main() -> int:
    prices = {"AAPL": 100.0, "MSFT": 200.0, "BTC-USD": 50_000.0, "OPT123": 3.00}
    broker = StubBroker(prices, Limits())

    # --- equity buy then partial sell (FIFO realized P&L) ---
    broker.submit(OrderIntent(symbol="AAPL", side="buy", notional=1_000.0, intent_key="e1"))
    check("equity qty after buy", broker.ledger["positions"]["equity:AAPL"]["qty"], 10.0)
    check("cash after equity buy", broker.ledger["cash"], 9_000.0)

    prices["AAPL"] = 120.0
    broker.submit(OrderIntent(symbol="AAPL", side="sell", qty=5.0, intent_key="e2"))
    check("equity realized P&L", broker.ledger["realized_pnl"], 100.0)
    check("equity qty after sell", broker.ledger["positions"]["equity:AAPL"]["qty"], 5.0)
    check("cash after equity sell", broker.ledger["cash"], 9_600.0)

    # --- duplicate intent is ignored ---
    result = broker.submit(OrderIntent(symbol="AAPL", side="sell", qty=5.0, intent_key="e2"))
    check("duplicate intent flagged", result.duplicate, True)
    check("cash unchanged by duplicate", broker.ledger["cash"], 9_600.0)

    # --- option buy uses the 100x multiplier and its own position key ---
    meta = {"expiry": "2026-10-16", "strike": 150.0, "type": "call"}
    broker.submit(
        OrderIntent(
            symbol="OPT123", side="buy", asset_class=pos.OPTION, qty=2.0, meta=meta, intent_key="o1"
        )
    )
    key = pos.position_key(pos.OPTION, "OPT123", meta)
    check("option position exists", key in broker.ledger["positions"], True)
    check("option cash debit (2 x $3 x 100)", broker.ledger["cash"], 9_000.0)

    prices["OPT123"] = 4.50
    broker.submit(
        OrderIntent(
            symbol="OPT123", side="sell", asset_class=pos.OPTION, qty=2.0, meta=meta, intent_key="o2"
        )
    )
    check("option realized P&L (1.50 x 2 x 100)", broker.ledger["realized_pnl"], 400.0)
    check("option position closed", key in broker.ledger["positions"], False)

    # --- crypto is fractional and keyed separately ---
    broker.submit(
        OrderIntent(symbol="BTC-USD", side="buy", asset_class=pos.CRYPTO, notional=500.0, intent_key="c1")
    )
    check("crypto qty", round(broker.ledger["positions"]["crypto:BTC-USD"]["qty"], 6), 0.01)
    check("crypto booked as crypto", broker.ledger["positions"]["crypto:BTC-USD"]["asset_class"], "crypto")

    # --- resting limit order fills only when the price comes to it ---
    broker.submit(
        OrderIntent(
            symbol="MSFT", side="buy", order_type="limit", qty=1.0, limit_price=180.0, intent_key="l1"
        )
    )
    check("limit order rests", len(broker.open_orders()), 1)
    broker.evaluate_resting_orders()
    check("limit still resting at $200", len(broker.open_orders()), 1)
    prices["MSFT"] = 175.0
    filled = broker.evaluate_resting_orders()
    check("limit fills at $175", len(filled), 1)
    check("limit order no longer open", len(broker.open_orders()), 0)
    check("MSFT position opened", "equity:MSFT" in broker.ledger["positions"], True)

    # --- cancel a resting order ---
    broker.submit(
        OrderIntent(
            symbol="AAPL", side="sell", order_type="limit", qty=1.0, limit_price=999.0, intent_key="l2"
        )
    )
    check("sell limit rests", len(broker.open_orders()), 1)
    cancelled = broker.cancel(symbol="AAPL")
    check("cancel returns the order", len(cancelled), 1)
    check("no open orders after cancel", len(broker.open_orders()), 0)

    # --- risk: max open positions blocks a new symbol ---
    class TightLimits(Limits):
        max_open_positions = 3

    broker._limits = TightLimits()
    blocked = broker.submit(OrderIntent(symbol="TSLA", side="buy", notional=100.0, intent_key="r1"))
    check("new position blocked at cap", blocked.status, "rejected")

    # --- an unfunded buy is rejected, not quietly shrunk to fit ---
    broke = StubBroker({"MSFT": 200.0}, Limits(), cash=500.0)
    broke.submit(OrderIntent(symbol="MSFT", side="buy", qty=10.0, intent_key="bp1"))
    last = broke.ledger["orders"][-1]
    check("unfunded buy rejected", last["status"], "rejected")
    check("rejection names the shortfall", "Insufficient paper buying power" in last["reason"], True)
    check("cash untouched", broke.ledger["cash"], 500.0)
    check("no partial position opened", len(broke.ledger["positions"]), 0)

    # --- option quantities are whole contracts ---
    opt = StubBroker({"OPT123": 3.00}, Limits())
    opt.submit(
        OrderIntent(symbol="OPT123", side="buy", asset_class=pos.OPTION, qty=2.7, intent_key="q1")
    )
    check("fractional contracts floored", opt.ledger["orders"][-1]["qty"], 2.0)
    tiny = opt.submit(
        OrderIntent(
            symbol="OPT123", side="buy", asset_class=pos.OPTION, notional=100.0, intent_key="q2"
        )
    )
    check("sub-contract notional rejected", tiny.status, "rejected")

    # --- selling with no position is rejected ---
    naked = StubBroker({"AAPL": 100.0}, Limits())
    naked.submit(OrderIntent(symbol="AAPL", side="sell", qty=1.0, intent_key="n1"))
    check("naked sell rejected", naked.ledger["orders"][-1]["status"], "rejected")

    # --- slippage and commission reach the fill ---
    from src.simulation.fills import FillModel

    friction = StubBroker(
        {"AAPL": 100.0}, Limits(), fill_model=FillModel(slippage_bps=50.0, commission_per_order=1.0)
    )
    friction.submit(OrderIntent(symbol="AAPL", side="buy", qty=10.0, intent_key="s1"))
    trade = friction.ledger["trades"][-1]
    check("buy slips up 0.50%", trade["price"], 100.5)
    check("commission charged", trade["commission"], 1.0)
    check("cash = 10000 - 1005 - 1", friction.ledger["cash"], 8_994.0)

    # --- T+N settlement holds sale proceeds out of buying power ---
    from src.simulation import accounting

    settle = StubBroker({"AAPL": 100.0}, Limits())
    settle.submit(OrderIntent(symbol="AAPL", side="buy", qty=10.0, intent_key="t1"))
    original_settlement = accounting.settlement_days
    accounting.settlement_days = lambda bot_id: 1
    try:
        settle.submit(OrderIntent(symbol="AAPL", side="sell", qty=10.0, intent_key="t2"))
    finally:
        accounting.settlement_days = original_settlement
    check("total cash includes proceeds", settle.ledger["cash"], 10_000.0)
    check("proceeds unsettled", accounting.unsettled_cash(settle.ledger), 1_000.0)
    check("available cash excludes unsettled", accounting.available_cash(settle.ledger), 9_000.0)

    # --- the hook's order is visible to the status-log handler, so it won't refill ---
    run = StubBroker({"AAPL": 100.0}, Limits())
    run.submit(OrderIntent(symbol="AAPL", side="buy", qty=1.0, run_id=42, intent_key="h1"))
    check("hook order seen for its run", run.has_order_for(42, "AAPL", "buy"), True)
    check("other run not matched", run.has_order_for(43, "AAPL", "buy"), False)
    check("other side not matched", run.has_order_for(42, "AAPL", "sell"), False)

    # --- v1 ledger migration ---
    upgraded = upgrade_ledger(
        {
            "cash": 500.0,
            "positions": {
                "NVDA": {"qty": 4.0, "avg_cost": 25.0},
                "GHOST": {"qty": 5.0},
            },
            "trades": [],
            "seeded": True,
        }
    )
    check("migrated version", upgraded["version"], 2)
    migrated = upgraded["positions"]["equity:NVDA"]
    check("migrated qty", migrated["qty"], 4.0)
    check("migrated avg cost", migrated["avg_cost"], 25.0)
    check("migrated lot count", len(migrated["lots"]), 1)
    check("cost-less position dropped", "equity:GHOST" in upgraded["positions"], False)
    check("v2 ledger not re-migrated", upgrade_ledger(upgraded)["version"], 2)

    # --- a migrated position still realizes against its original basis ---
    after = StubBroker({"NVDA": 40.0}, Limits())
    after.ledger = upgraded
    after.submit(OrderIntent(symbol="NVDA", side="sell", qty=4.0, intent_key="mg1"))
    check("realized against migrated basis", after.ledger["realized_pnl"], 60.0)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("All simulation regression checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
