# Crypto

Spot crypto only, 24/7. Preview `preview_crypto_order` before any place.

- Each cycle: portfolio, `crypto_positions`, pair quotes, 1h technicals (open/close, SMA-20, RSI-14). Uncertain → no action + `log_event`.
- Universe: Tradable crypto. Normalize pairs as `BTC-USD`.
- Entry (flat in the pair): 1h close below SMA-20 and RSI(14) < 40, or a ≥3% dip from the 24h high. One new pair per cycle.
- Size: at most 20% of portfolio equity in any pair; `max_crypto_notional_usd` is the hard cap. Max 4 pairs.
- DCA once per pair per cycle if mark is ≥6% below average cost (buy half the current position value).
- Take profit at +6% vs average cost. Stop at −15%. Cancel working orders that survive a cycle.
- Do not guess. Thin books or missing quotes → skip.
