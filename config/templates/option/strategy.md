# Options

Long single-leg options only. Skip when the cash session is closed. Never sell premium. Never exercise — close the contract. Review `review_option_order` before any place.

- Each cycle: portfolio, `option_positions`, underlying quotes, 1h technicals, chain near the money. Uncertain → no action + `log_event`.
- Universe: the configured Index options list. Prefer liquid underlyings (SPY, QQQ, IWM) when the list is large.
- Entry: buy one call or put when the underlying is in a ≥2% dip (calls on a bounce setup; puts only if the 1h close is below SMA-20 and RSI < 35). Skip if implied vol looks like a blow-off or the bid/ask is wider than 8% of mid.
- Structure: 1 contract, DTE 21–45, defined risk. Notional ≤ `max_option_notional_usd`.
- Exit: take profit when the contract is +40% vs fill, or the thesis is done. Stop if the contract is −50% or DTE < 7. Close; do not roll into a new short.
- One new contract per cycle. Cancel working orders that survive a cycle.
