"""
Backtest of the BTC mid-term Composite Volume Profile strategy.

Philosophy (to avoid curve fitting):
  - Very small set of round, natural parameters a discretionary
    trader would pick (50-bin VP, 70 % value area, 50-day EMA,
    4-week composite, 1R / 3R targets, 60-bar time stop).
  - Perfect L/S symmetry.
  - One concept per layer:
        D1 trend filter    : close vs 50-day EMA
        D1 CVD filter      : daily net delta vs its 50-day EMA
        Volume Profile     : composite of previous 4 completed weeks
        H4 execution       : fresh close beyond the composite VAH/VAL
  - No look-ahead: daily biases are taken from the *previous* day's
    close, the composite is built from the N previous completed
    weeks only.
  - Risk management: fixed 1 % equity risk per trade, stop at the
    composite POC, scale 50 % at 1R (move the stop to break-even),
    let the rest ride to 3R or be closed by the 60-bar time stop.
  - In-sample / out-of-sample split at 2024-01-01 – both periods
    must be independently profitable for the strategy to ship.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

DATA_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_15m() -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(DATA_DIR, "BTCUSDT-15m-*.csv")))
    frames = []
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_base", "taker_quote", "ignore",
    ]
    for f in files:
        # Some monthly files include a header row, others don't – detect it.
        with open(f, "r") as fh:
            first = fh.readline()
        if first.startswith("open_time"):
            df = pd.read_csv(f)
            df = df.rename(columns={"count": "trades",
                                    "taker_buy_volume": "taker_base",
                                    "taker_buy_quote_volume": "taker_quote"})
        else:
            df = pd.read_csv(f, header=None, names=cols)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.drop_duplicates("open_time").sort_values("ts").reset_index(drop=True)
    df["taker_sell"] = df["volume"] - df["taker_base"]
    df["delta"] = df["taker_base"] - df["taker_sell"]     # proxy CVD delta
    return df[["ts", "open", "high", "low", "close", "volume", "delta"]]


def resample(df15: pd.DataFrame, rule: str) -> pd.DataFrame:
    r = df15.set_index("ts").resample(rule, label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        delta=("delta", "sum"),
    ).dropna()
    return r.reset_index()


# ---------------------------------------------------------------------------
# Volume Profile utilities
# ---------------------------------------------------------------------------
def volume_profile(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    bins: int = 50,
    value_area_pct: float = 0.70,
):
    """Return (POC, VAH, VAL) for a slice of bars.

    Distributes each bar's volume over the bins it spans, then walks
    bins outward from the POC until `value_area_pct` of total volume
    is captured (classical "70 % value area" definition).
    """
    lo = float(np.min(lows))
    hi = float(np.max(highs))
    if hi <= lo:
        return lo, lo, lo
    edges = np.linspace(lo, hi, bins + 1)
    prof = np.zeros(bins)
    for h, l, v in zip(highs, lows, volumes):
        i_lo = max(0, int((l - lo) / (hi - lo) * bins))
        i_hi = min(bins - 1, int((h - lo) / (hi - lo) * bins))
        span = i_hi - i_lo + 1
        if span <= 0:
            continue
        prof[i_lo:i_hi + 1] += v / span
    poc_idx = int(np.argmax(prof))
    total = prof.sum()
    target = total * value_area_pct
    acc = prof[poc_idx]
    lo_idx = hi_idx = poc_idx
    while acc < target and (lo_idx > 0 or hi_idx < bins - 1):
        up_val = prof[hi_idx + 1] if hi_idx + 1 < bins else -1.0
        dn_val = prof[lo_idx - 1] if lo_idx - 1 >= 0 else -1.0
        if up_val >= dn_val:
            hi_idx += 1
            acc += prof[hi_idx]
        else:
            lo_idx -= 1
            acc += prof[lo_idx]
    mid = (edges[:-1] + edges[1:]) / 2.0
    return float(mid[poc_idx]), float(mid[hi_idx]), float(mid[lo_idx])


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    side: str
    entry_time: pd.Timestamp
    entry: float
    stop: float
    tp1: float
    tp2: float
    initial_stop: float = 0.0
    tp1_hit: bool = False
    exit_time: Optional[pd.Timestamp] = None
    exit: Optional[float] = None
    reason: str = ""
    r_mult: float = 0.0


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[tuple] = field(default_factory=list)


def run_backtest(
    df15: pd.DataFrame,
    bins: int = 50,
    value_area: float = 0.70,
    ema_len: int = 50,
    atr_len: int = 14,
    rr: float = 3.0,
    risk_frac: float = 0.01,
    start_equity: float = 10_000.0,
    max_hold_bars: int = 60,       # ≈10 days on H4
) -> BacktestResult:
    """Weekly Value-Area breakout with daily trend + CVD confirmation.

    Long setup:
      - D1 close > D1 EMA50     (trend bias)
      - D1 CVD > CVD EMA50      (aggression confirms trend)
      - H4 close > prior week VAH, while previous H4 close was <= VAH
        (fresh breakout from the prior week's value area)
      - Stop = prior week POC (objective "fair value" invalidation)
      - Target = entry + rr * (entry - stop)
      - Time stop: close position after `max_hold_bars` H4 bars

    Short setup: symmetric around VAL.
    """
    h4 = resample(df15, "4h")
    d1 = resample(df15, "1D")

    # Daily indicators
    d1["ema"] = d1["close"].ewm(span=ema_len, adjust=False).mean()
    d1["bias"] = np.where(d1["close"] > d1["ema"], 1, -1)
    d1["cvd"] = d1["delta"].cumsum()
    d1["cvd_ma"] = d1["cvd"].ewm(span=ema_len, adjust=False).mean()
    d1["cvd_bias"] = np.where(d1["cvd"] > d1["cvd_ma"], 1, -1)
    d1_bias_at = dict(zip(d1["ts"], d1["bias"]))
    d1_cvd_bias_at = dict(zip(d1["ts"], d1["cvd_bias"]))

    # H4 ATR for sanity checks
    tr = np.maximum(
        h4["high"] - h4["low"],
        np.maximum(
            (h4["high"] - h4["close"].shift()).abs(),
            (h4["low"] - h4["close"].shift()).abs(),
        ),
    )
    h4["atr"] = tr.rolling(atr_len).mean()

    # Composite volume profile: previous N completed weeks.
    # Using a multi-week composite smooths out single-week noise and is
    # the standard "Composite Volume Profile" concept the user asked for.
    composite_weeks = 4
    h4["week"] = h4["ts"].dt.tz_convert("UTC").dt.to_period("W-MON")
    weeks = sorted(h4["week"].unique())
    wk_bars = {w: h4[h4["week"] == w] for w in weeks}
    # Map each week -> composite VP of the previous `composite_weeks` weeks
    composite_vp: dict = {}
    for idx, w in enumerate(weeks):
        if idx < composite_weeks:
            continue
        src = pd.concat([wk_bars[weeks[idx - k - 1]] for k in range(composite_weeks)])
        composite_vp[w] = volume_profile(
            src["high"].values, src["low"].values, src["close"].values, src["volume"].values,
            bins=bins, value_area_pct=value_area,
        )
    prev_week = composite_vp  # reused name: mapping from current week -> composite VP

    result = BacktestResult()
    equity = start_equity
    open_trade: Optional[Trade] = None
    bars_in_trade = 0

    for i in range(1, len(h4)):
        row = h4.iloc[i]
        ts = row["ts"]
        atr = row["atr"]
        if not np.isfinite(atr) or atr <= 0:
            continue
        week = row["week"]
        if week not in prev_week:
            continue
        poc, vah, val = prev_week[week]

        # Use yesterday's bias (no look-ahead).
        d_key = pd.Timestamp(ts.date(), tz="UTC") - pd.Timedelta(days=1)
        bias = d1_bias_at.get(d_key)
        cvd_bias = d1_cvd_bias_at.get(d_key)
        if bias is None or cvd_bias is None:
            continue

        # -----------------------------------------------------------
        # Manage existing trade – scale 50% at 1R, move stop to BE,
        # let the rest ride to TP2 (3R) or time stop.
        # -----------------------------------------------------------
        if open_trade is not None:
            bars_in_trade += 1
            hi, lo, cl = row["high"], row["low"], row["close"]
            if open_trade.side == "long":
                # TP1 (half size) -> move stop to BE
                if not open_trade.tp1_hit and hi >= open_trade.tp1:
                    open_trade.tp1_hit = True
                    open_trade.stop = open_trade.entry
                if lo <= open_trade.stop:
                    open_trade.exit_time = ts
                    open_trade.exit = open_trade.stop
                    open_trade.reason = "stop"
                elif hi >= open_trade.tp2:
                    open_trade.exit_time = ts
                    open_trade.exit = open_trade.tp2
                    open_trade.reason = "tp"
                elif bars_in_trade >= max_hold_bars:
                    open_trade.exit_time = ts
                    open_trade.exit = cl
                    open_trade.reason = "time"
            else:
                if not open_trade.tp1_hit and lo <= open_trade.tp1:
                    open_trade.tp1_hit = True
                    open_trade.stop = open_trade.entry
                if hi >= open_trade.stop:
                    open_trade.exit_time = ts
                    open_trade.exit = open_trade.stop
                    open_trade.reason = "stop"
                elif lo <= open_trade.tp2:
                    open_trade.exit_time = ts
                    open_trade.exit = open_trade.tp2
                    open_trade.reason = "tp"
                elif bars_in_trade >= max_hold_bars:
                    open_trade.exit_time = ts
                    open_trade.exit = cl
                    open_trade.reason = "time"

            if open_trade.exit is not None:
                # Half closed at TP1, half at final exit.
                init_risk = abs(open_trade.entry - open_trade.initial_stop)
                remainder = (open_trade.exit - open_trade.entry) / init_risk
                if open_trade.side == "short":
                    remainder = -remainder
                half = 1.0 if open_trade.tp1_hit else 0.0   # +1R on first half
                r = 0.5 * half + 0.5 * remainder
                open_trade.r_mult = r
                equity *= 1.0 + risk_frac * r
                result.trades.append(open_trade)
                result.equity_curve.append((ts, equity))
                open_trade = None
                bars_in_trade = 0

        if open_trade is not None:
            continue

        # -----------------------------------------------------------
        # Entry – fresh weekly value-area breakout with trend bias
        # -----------------------------------------------------------
        prev = h4.iloc[i - 1]
        hi, lo, cl, op = row["high"], row["low"], row["close"], row["open"]

        # Long: bias up, H4 closes above VAH for the first time
        if bias > 0 and cvd_bias > 0:
            fresh_break = cl > vah and prev["close"] <= vah
            if fresh_break:
                entry = cl
                stop = poc
                if entry - stop > 0.5 * atr:       # require meaningful risk
                    tp2 = entry + rr * (entry - stop)
                    tp1 = entry + (entry - stop)
                    open_trade = Trade("long", ts, entry, stop, tp1, tp2,
                                       initial_stop=stop)
                    bars_in_trade = 0

        elif bias < 0 and cvd_bias < 0:
            fresh_break = cl < val and prev["close"] >= val
            if fresh_break:
                entry = cl
                stop = poc
                if stop - entry > 0.5 * atr:
                    tp2 = entry - rr * (stop - entry)
                    tp1 = entry - (stop - entry)
                    open_trade = Trade("short", ts, entry, stop, tp1, tp2,
                                       initial_stop=stop)
                    bars_in_trade = 0

    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def report(r: BacktestResult, start: float = 10_000.0) -> str:
    if not r.trades:
        return "No trades."
    pnl = np.array([t.r_mult for t in r.trades])
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    equity = start
    eq_curve = []
    peak = equity
    max_dd = 0.0
    for t in r.trades:
        equity *= 1.0 + 0.01 * t.r_mult
        eq_curve.append(equity)
        peak = max(peak, equity)
        dd = (peak - equity) / peak
        max_dd = max(max_dd, dd)

    out = []
    out.append(f"Trades           : {len(r.trades)}")
    out.append(f"Win rate         : {len(wins)/len(pnl)*100:.1f}%")
    out.append(f"Avg R            : {pnl.mean():.3f}")
    out.append(f"Avg win R        : {wins.mean() if len(wins) else 0:.3f}")
    out.append(f"Avg loss R       : {losses.mean() if len(losses) else 0:.3f}")
    if losses.sum() != 0:
        out.append(f"Profit factor    : {wins.sum() / -losses.sum():.3f}")
    out.append(f"Total R          : {pnl.sum():.2f}")
    out.append(f"Final equity     : {equity:,.2f} (from {start:,.0f})")
    out.append(f"Max drawdown     : {max_dd*100:.1f}%")
    if r.trades:
        out.append(f"First trade      : {r.trades[0].entry_time}")
        out.append(f"Last trade       : {r.trades[-1].exit_time}")
    return "\n".join(out)


if __name__ == "__main__":
    print("Loading data ...")
    df15 = load_15m()
    print(f"Rows: {len(df15):,}  Range: {df15['ts'].iloc[0]} -> {df15['ts'].iloc[-1]}")

    # ----- In-sample vs out-of-sample split (2021-2023 vs 2024-2026) -----
    split = pd.Timestamp("2024-01-01", tz="UTC")
    in_sample = df15[df15["ts"] < split].reset_index(drop=True)
    oos = df15[df15["ts"] >= split].reset_index(drop=True)

    print("\n=== In-sample  (2021-2023) ===")
    r_in = run_backtest(in_sample)
    print(report(r_in))

    print("\n=== Out-of-sample (2024+) ===")
    r_oos = run_backtest(oos)
    print(report(r_oos))

    print("\n=== Full history ===")
    r_all = run_backtest(df15)
    print(report(r_all))
