# Equities

US cash equities only. Skip when the cash session is closed. Review `review_equity_order` before any place.

- Each cycle: portfolio, positions, quotes, 1h technicals (open/close, SMA-20, RSI-14), last action. Uncertain → no action + `log_event`.
- Entry (flat in the symbol): 1h close below SMA-20 and RSI(14) < 35, or a ≥2% dip from the 1d high. One new name per cycle.
- Size: at most 20% of portfolio equity in any symbol; `max_order_notional_usd` is the hard cap. Max 5 names.
- DCA once per symbol per cycle if mark is ≥5% below average cost (buy half the current position value).
- Take profit at +4% vs average cost. Stop at −8%. Cancel working orders that survive a cycle.
- Do not guess. If the tape and indicators disagree, skip.
