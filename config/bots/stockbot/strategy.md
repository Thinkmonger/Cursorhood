-Loop: 1h cycle. Sync-fetch equity, positions, and live quotes.
-Data Fallback: If 1h 20SMA/RSI/volume are available, apply Entry criteria below. If indicators are unavailable, fall back to evaluating 24h price movement or simple distance from recent price range.
-Entry: 
  * With Indicators: If holdings == 0 AND 1h price < 1h 20SMA AND 1h RSI(14) < 40.
  * Without Indicators (Fallback): If holdings == 0 AND current mark price is in a daily dip (>= 2% below 24h peak).
-Limits: Max exposure per symbol <= 25% portfolio equity ($625 per symbol on $2500 equity).
-DCA: If price drops >= 5% below Average Cost -> Buy 50% of current position value. Max 1 DCA fill per symbol per cycle.
-Profit Taking: If current mark price >= Average Cost + 3.0% -> Execute Limit Sell or Market Exit.
-Stop Loss: If current mark price <= Average Cost - 12.0% -> Market liquidate position.
-Orders: Cancel unexecuted orders after 1 cycle.