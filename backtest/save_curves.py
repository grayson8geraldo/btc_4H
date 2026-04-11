"""Save per-strategy equity curves and trade logs for the tournament report."""

from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_loader import load_btc_15m, train_test_split
from engine import run_backtest
from strategies import STRATEGIES


def main() -> None:
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
    os.makedirs(out_dir, exist_ok=True)

    df = load_btc_15m()
    _, test = train_test_split(df, 0.70)

    curves = {}
    trade_rows = []
    for name, fn in STRATEGIES.items():
        r = run_backtest(test, fn, name)
        # Resample the equity curve to daily for compact storage
        daily = r.equity_curve.resample("1D").last().dropna()
        curves[name] = daily
        for t in r.trades:
            trade_rows.append(
                {
                    "strategy": name,
                    "side": "long" if t.side == 1 else "short",
                    "entry_time": t.entry_time,
                    "entry_price": round(t.entry_price, 2),
                    "exit_time": t.exit_time,
                    "exit_price": round(t.exit_price, 2),
                    "pnl": round(t.pnl, 3),
                    "reason": t.reason,
                }
            )

    curves_df = pd.DataFrame(curves)
    curves_df.index.name = "date"
    curves_df.to_csv(os.path.join(out_dir, "test_equity_curves_daily.csv"))
    print(f"Saved equity curves -> {os.path.join(out_dir, 'test_equity_curves_daily.csv')}")

    trades_df = pd.DataFrame(trade_rows)
    trades_df.to_csv(os.path.join(out_dir, "test_trades.csv"), index=False)
    print(f"Saved {len(trades_df)} trades -> {os.path.join(out_dir, 'test_trades.csv')}")


if __name__ == "__main__":
    main()
