"""Trade-level analysis of the baseline strategy.

Dumps every trade, classifies each losing / break-even trade by the
prevailing regime (ATR expansion, EMA distance, composite VA width)
so we can figure out WHAT filters would eliminate them without
killing the winners.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import load_15m, resample, volume_profile, run_backtest, Trade


def annotate_trades():
    df15 = load_15m()
    h4 = resample(df15, "4h")
    d1 = resample(df15, "1D")

    # H4 ATR + slope
    tr = np.maximum(
        h4["high"] - h4["low"],
        np.maximum(
            (h4["high"] - h4["close"].shift()).abs(),
            (h4["low"] - h4["close"].shift()).abs(),
        ),
    )
    h4["atr"] = tr.rolling(14).mean()
    h4["atr_ma"] = h4["atr"].rolling(50).mean()
    h4["ema20"] = h4["close"].ewm(span=20, adjust=False).mean()
    h4["ema50"] = h4["close"].ewm(span=50, adjust=False).mean()
    h4["ema200"] = h4["close"].ewm(span=200, adjust=False).mean()

    # Daily context
    d1["ema50"] = d1["close"].ewm(span=50, adjust=False).mean()
    d1["ema_slope"] = d1["ema50"].pct_change(5)
    d1_ctx = dict(zip(d1["ts"], zip(d1["ema50"], d1["ema_slope"])))

    # Composite profile
    h4["week"] = h4["ts"].dt.tz_convert("UTC").dt.to_period("W-MON")
    weeks = sorted(h4["week"].unique())
    wk_bars = {w: h4[h4["week"] == w] for w in weeks}
    composite_vp: dict = {}
    for idx, w in enumerate(weeks):
        if idx < 4:
            continue
        src = pd.concat([wk_bars[weeks[idx - k - 1]] for k in range(4)])
        composite_vp[w] = volume_profile(
            src["high"].values, src["low"].values, src["close"].values, src["volume"].values,
        )

    r = run_backtest(df15)
    print(f"Baseline: {len(r.trades)} trades, total {sum(t.r_mult for t in r.trades):.2f}R")

    def row_for(ts):
        return h4[h4["ts"] == ts].iloc[0]

    rows = []
    for t in r.trades:
        try:
            row = row_for(t.entry_time)
        except IndexError:
            continue
        atr = row["atr"]
        atr_ma = row["atr_ma"]
        week = row["week"]
        poc, vah, val = composite_vp.get(week, (np.nan, np.nan, np.nan))
        va_width = (vah - val) / row["close"] if vah > val else np.nan
        d_key = pd.Timestamp(row["ts"].date(), tz="UTC") - pd.Timedelta(days=1)
        ema_d, slope = d1_ctx.get(d_key, (np.nan, np.nan))
        dist_ema = (row["close"] - ema_d) / ema_d
        # risk as %
        risk_pct = abs(t.entry - t.initial_stop) / t.entry
        duration_bars = None
        if t.exit_time is not None:
            duration_bars = ((t.exit_time - t.entry_time).total_seconds() / 3600) / 4

        rows.append(dict(
            side=t.side,
            entry_time=t.entry_time,
            r=t.r_mult,
            reason=t.reason,
            atr_ratio=atr / atr_ma if atr_ma else np.nan,  # > 1 = expanding
            va_width_pct=va_width * 100,
            dist_ema_pct=dist_ema * 100,
            ema_slope_pct=slope * 100 if slope is not None else np.nan,
            risk_pct=risk_pct * 100,
            dur_bars=duration_bars,
            ema20_above_50=row["ema20"] > row["ema50"],
            ema50_above_200=row["ema50"] > row["ema200"],
        ))

    tdf = pd.DataFrame(rows)
    print(tdf.to_string())
    print()

    # Compare winners vs losers
    wins = tdf[tdf["r"] > 0]
    losses = tdf[tdf["r"] <= 0]
    print(f"\n=== Mean features: winners vs losers ===")
    feats = ["atr_ratio", "va_width_pct", "dist_ema_pct", "ema_slope_pct", "risk_pct", "dur_bars"]
    for f in feats:
        print(f"{f:20s} win={wins[f].mean():7.3f}  loss={losses[f].mean():7.3f}")

    # Alignment with short-term trend (ema20 vs ema50)
    print(f"\nema20>ema50 at entry: win {wins['ema20_above_50'].mean():.2f} vs loss {losses['ema20_above_50'].mean():.2f}")

    # by reason
    print("\nReasons:")
    print(tdf.groupby("reason")["r"].describe()[["count", "mean", "min", "max"]])

    return tdf


if __name__ == "__main__":
    annotate_trades()
