"""Systematic improvement search for the BTC Composite Volume Profile strategy.

For every variant we run the same backtest engine and report:
  * In-sample window (2021-2023)
  * Out-of-sample window (2024-2026)
  * Per-year breakdown
  * Aggregate metrics (trades, win rate, PF, total R, max drawdown)

The variants are NOT parameter sweeps – each one tests an *idea*:
  - baseline                   : current live strategy
  - chop filter (ADX)          : skip days when daily ADX < 20
  - slope filter               : require steep D1 EMA50 slope
  - tight-risk filter          : skip trades where stop < 1 ATR
  - cooldown after loss        : sit out 5 bars after a stop-out
  - trailing stop on runner    : trail the half-runner with ATR
  - higher R target            : TP2 = 4R / 5R
  - longer composite           : 8-week composite instead of 4
  - conviction sizing          : double risk when D1 slope & CVD strong
  - stacked best               : combine the winning ideas
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

from backtest import load_15m, resample, volume_profile


@dataclass
class Trade:
    side: str
    entry_time: pd.Timestamp
    entry: float
    stop: float
    initial_stop: float
    tp1: float
    tp2: float
    size: float = 1.0                # relative risk size (1.0 == 1R baseline)
    tp1_hit: bool = False
    exit_time: Optional[pd.Timestamp] = None
    exit: Optional[float] = None
    reason: str = ""
    r_mult: float = 0.0


def wilder_rma(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1.0 / length, adjust=False).mean()


def daily_adx(d1: pd.DataFrame, length: int = 14) -> pd.Series:
    up = d1["high"].diff()
    dn = -d1["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum(
        d1["high"] - d1["low"],
        np.maximum(
            (d1["high"] - d1["close"].shift()).abs(),
            (d1["low"] - d1["close"].shift()).abs(),
        ),
    )
    atr = wilder_rma(tr, length)
    plus_di = 100 * wilder_rma(pd.Series(plus_dm, index=d1.index), length) / atr
    minus_di = 100 * wilder_rma(pd.Series(minus_dm, index=d1.index), length) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder_rma(dx.fillna(0), length)


@dataclass
class Params:
    bins: int = 50
    value_area: float = 0.70
    composite_weeks: int = 4
    ema_len: int = 50
    atr_len: int = 14
    rr: float = 3.0
    risk_frac: float = 0.01
    max_hold_bars: int = 60
    min_stop_atr: float = 0.5

    # New switches
    use_chop_filter: bool = False
    adx_min: float = 20.0
    use_slope_filter: bool = False
    slope_min_pct: float = 0.3
    cooldown_after_loss: int = 0
    trail_runner_atr: float = 0.0       # 0 = disabled
    conviction_size: bool = False
    strong_slope_pct: float = 0.8
    no_reentry_same_week: bool = False


@dataclass
class Result:
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[tuple] = field(default_factory=list)


def run(df15: pd.DataFrame, P: Params) -> Result:
    h4 = resample(df15, "4h")
    d1 = resample(df15, "1D")

    d1["ema"] = d1["close"].ewm(span=P.ema_len, adjust=False).mean()
    d1["bias"] = np.where(d1["close"] > d1["ema"], 1, -1)
    d1["ema_slope"] = d1["ema"].pct_change(5) * 100  # 5-day slope in %
    d1["cvd"] = ((d1["close"] > d1["close"].shift()).astype(int) - (d1["close"] < d1["close"].shift()).astype(int)) * d1["volume"]
    d1["cvd"] = d1["cvd"].cumsum()
    d1["cvd_ma"] = d1["cvd"].ewm(span=P.ema_len, adjust=False).mean()
    d1["cvd_bias"] = np.where(d1["cvd"] > d1["cvd_ma"], 1, -1)
    d1["adx"] = daily_adx(d1)

    d1_at = {}
    for _, r in d1.iterrows():
        d1_at[r["ts"]] = (r["bias"], r["cvd_bias"], r["ema_slope"], r["adx"])

    tr = np.maximum(
        h4["high"] - h4["low"],
        np.maximum(
            (h4["high"] - h4["close"].shift()).abs(),
            (h4["low"] - h4["close"].shift()).abs(),
        ),
    )
    h4["atr"] = tr.rolling(P.atr_len).mean()
    h4["ema20"] = h4["close"].ewm(span=20, adjust=False).mean()

    h4["week"] = h4["ts"].dt.tz_convert("UTC").dt.to_period("W-MON")
    weeks = sorted(h4["week"].unique())
    wk_bars = {w: h4[h4["week"] == w] for w in weeks}
    composite_vp: dict = {}
    for idx, w in enumerate(weeks):
        if idx < P.composite_weeks:
            continue
        src = pd.concat([wk_bars[weeks[idx - k - 1]] for k in range(P.composite_weeks)])
        composite_vp[w] = volume_profile(
            src["high"].values, src["low"].values, src["close"].values, src["volume"].values,
            bins=P.bins, value_area_pct=P.value_area,
        )

    result = Result()
    equity = 10_000.0
    open_trade: Optional[Trade] = None
    bars_in_trade = 0
    cooldown = 0
    last_trade_week = None
    last_trade_lost = False

    for i in range(1, len(h4)):
        row = h4.iloc[i]
        ts = row["ts"]
        atr = row["atr"]
        if not np.isfinite(atr) or atr <= 0:
            continue
        week = row["week"]
        if week not in composite_vp:
            continue
        poc, vah, val = composite_vp[week]

        d_key = pd.Timestamp(ts.date(), tz="UTC") - pd.Timedelta(days=1)
        ctx = d1_at.get(d_key)
        if ctx is None:
            continue
        bias, cvd_bias, slope, adx = ctx
        if np.isnan(slope) or np.isnan(adx):
            continue

        # Manage open trade ------------------------------------------------
        if open_trade is not None:
            bars_in_trade += 1
            hi, lo, cl = row["high"], row["low"], row["close"]
            # trailing stop on the runner half (after TP1 hit)
            if P.trail_runner_atr > 0 and open_trade.tp1_hit:
                if open_trade.side == "long":
                    trail = cl - P.trail_runner_atr * atr
                    if trail > open_trade.stop:
                        open_trade.stop = trail
                else:
                    trail = cl + P.trail_runner_atr * atr
                    if trail < open_trade.stop:
                        open_trade.stop = trail

            if open_trade.side == "long":
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
                elif bars_in_trade >= P.max_hold_bars:
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
                elif bars_in_trade >= P.max_hold_bars:
                    open_trade.exit_time = ts
                    open_trade.exit = cl
                    open_trade.reason = "time"

            if open_trade.exit is not None:
                init_risk = abs(open_trade.entry - open_trade.initial_stop)
                remainder = (open_trade.exit - open_trade.entry) / init_risk
                if open_trade.side == "short":
                    remainder = -remainder
                half = 1.0 if open_trade.tp1_hit else 0.0
                base_r = 0.5 * half + 0.5 * remainder
                # scale by conviction size
                r = base_r * open_trade.size
                open_trade.r_mult = r
                equity *= 1.0 + P.risk_frac * r
                result.trades.append(open_trade)
                result.equity_curve.append((ts, equity))
                last_trade_lost = r < 0
                cooldown = P.cooldown_after_loss if last_trade_lost else 0
                last_trade_week = week
                open_trade = None
                bars_in_trade = 0

        if open_trade is not None:
            continue
        if cooldown > 0:
            cooldown -= 1
            continue
        if P.no_reentry_same_week and last_trade_week == week and last_trade_lost:
            continue

        # Filters -----------------------------------------------------------
        if P.use_chop_filter and adx < P.adx_min:
            continue
        if P.use_slope_filter and abs(slope) < P.slope_min_pct:
            continue

        # Entry logic -------------------------------------------------------
        prev = h4.iloc[i - 1]
        cl = row["close"]

        def _open(side):
            nonlocal open_trade, bars_in_trade
            entry = cl
            stop = poc
            if side == "long":
                risk = entry - stop
                if risk <= P.min_stop_atr * atr:
                    return
                tp1 = entry + risk
                tp2 = entry + P.rr * risk
            else:
                risk = stop - entry
                if risk <= P.min_stop_atr * atr:
                    return
                tp1 = entry - risk
                tp2 = entry - P.rr * risk

            size = 1.0
            if P.conviction_size:
                strong = abs(slope) >= P.strong_slope_pct and adx >= 25
                size = 1.5 if strong else 0.75
            open_trade = Trade(side, ts, entry, stop,
                               initial_stop=stop, tp1=tp1, tp2=tp2, size=size)
            bars_in_trade = 0

        if bias > 0 and cvd_bias > 0:
            if cl > vah and prev["close"] <= vah:
                _open("long")
        elif bias < 0 and cvd_bias < 0:
            if cl < val and prev["close"] >= val:
                _open("short")

    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def summary(res: Result, label: str = "") -> dict:
    if not res.trades:
        return {"label": label, "trades": 0, "wr": 0, "pf": 0, "totalR": 0, "dd": 0}
    pnl = np.array([t.r_mult for t in res.trades])
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    wr = len(wins) / len(pnl) * 100
    pf = wins.sum() / -losses.sum() if losses.sum() != 0 else np.inf
    total = pnl.sum()
    # drawdown
    equity = 10_000.0
    peak = equity
    max_dd = 0.0
    for r in pnl:
        equity *= 1.0 + 0.01 * r
        peak = max(peak, equity)
        dd = (peak - equity) / peak
        max_dd = max(max_dd, dd)
    return {
        "label": label,
        "trades": len(pnl),
        "wr": wr,
        "pf": pf,
        "totalR": total,
        "dd": max_dd * 100,
    }


def split_results(df15: pd.DataFrame, P: Params) -> dict:
    split = pd.Timestamp("2024-01-01", tz="UTC")
    res_in  = run(df15[df15["ts"] <  split].reset_index(drop=True), P)
    res_out = run(df15[df15["ts"] >= split].reset_index(drop=True), P)
    res_all = run(df15, P)
    return {
        "in": summary(res_in, "IN"),
        "out": summary(res_out, "OUT"),
        "all": summary(res_all, "ALL"),
        "year": yearly_breakdown(res_all),
    }


def yearly_breakdown(res: Result) -> dict:
    by = {}
    for t in res.trades:
        y = t.entry_time.year
        by.setdefault(y, []).append(t.r_mult)
    return {y: (len(v), round(sum(v), 2)) for y, v in sorted(by.items())}


def pretty(name: str, r: dict):
    I, O, A = r["in"], r["out"], r["all"]
    print(f"\n--- {name} ---")
    print(f"  IN  : n={I['trades']:3d} WR={I['wr']:5.1f}% PF={I['pf']:5.2f} totalR={I['totalR']:+6.2f} DD={I['dd']:4.1f}%")
    print(f"  OUT : n={O['trades']:3d} WR={O['wr']:5.1f}% PF={O['pf']:5.2f} totalR={O['totalR']:+6.2f} DD={O['dd']:4.1f}%")
    print(f"  ALL : n={A['trades']:3d} WR={A['wr']:5.1f}% PF={A['pf']:5.2f} totalR={A['totalR']:+6.2f} DD={A['dd']:4.1f}%")
    print(f"  Yearly: {r['year']}")


if __name__ == "__main__":
    print("Loading data ...")
    df15 = load_15m()

    variants = {
        "baseline": Params(),
        "chop_adx20": Params(use_chop_filter=True, adx_min=20),
        "chop_adx25": Params(use_chop_filter=True, adx_min=25),
        "slope_0.3":  Params(use_slope_filter=True, slope_min_pct=0.3),
        "min_stop_1ATR": Params(min_stop_atr=1.0),
        "min_stop_0.8ATR": Params(min_stop_atr=0.8),
        "cooldown_5":  Params(cooldown_after_loss=5),
        "cooldown_10": Params(cooldown_after_loss=10),
        "trail_1.5ATR":  Params(trail_runner_atr=1.5),
        "trail_2ATR":    Params(trail_runner_atr=2.0),
        "trail_3ATR":    Params(trail_runner_atr=3.0),
        "rr_4":       Params(rr=4.0),
        "rr_5":       Params(rr=5.0),
        "rr_6":       Params(rr=6.0),
        "composite_8w": Params(composite_weeks=8),
        "composite_6w": Params(composite_weeks=6),
        "composite_3w": Params(composite_weeks=3),
        "composite_2w": Params(composite_weeks=2),
        "conviction":   Params(conviction_size=True),
        "no_reentry":   Params(no_reentry_same_week=True),
        "no_cvd":       Params(),     # placeholder overwritten below
    }
    del variants["no_cvd"]

    results = {}
    for name, P in variants.items():
        results[name] = split_results(df15, P)
        pretty(name, results[name])

    # Stacked combinations based on what wins
    print("\n\n=== Stacked combinations ===")
    combos = {
        # Best single-switch ideas combined additively
        "stack_A": Params(rr=5.0, cooldown_after_loss=5),
        "stack_B": Params(rr=5.0, cooldown_after_loss=5, composite_weeks=3),
        "stack_C": Params(rr=5.0, cooldown_after_loss=5, composite_weeks=2),
        "stack_D": Params(rr=4.0, cooldown_after_loss=5),
        "stack_E": Params(rr=5.0, cooldown_after_loss=5, trail_runner_atr=2.0),
        "stack_F": Params(rr=5.0, cooldown_after_loss=5, composite_weeks=3, trail_runner_atr=2.0),
        "stack_G": Params(rr=5.0, cooldown_after_loss=5, composite_weeks=3, use_slope_filter=True, slope_min_pct=0.2),
        "stack_H": Params(rr=5.0, cooldown_after_loss=5, min_stop_atr=0.8),
        "stack_I": Params(rr=5.0, composite_weeks=3),
        "stack_J": Params(rr=5.0, composite_weeks=2),
        "stack_K": Params(rr=6.0, cooldown_after_loss=5, composite_weeks=3),
    }
    for name, P in combos.items():
        r = split_results(df15, P)
        results[name] = r
        pretty(name, r)

    print("\n\n=== Ranking by full-history total R (only variants profitable on OUT) ===")
    rows = []
    for name, r in results.items():
        if r["out"]["totalR"] > 0 and r["in"]["totalR"] > 0:
            rows.append((name, r["all"]["totalR"], r["all"]["dd"], r["out"]["totalR"], r["in"]["totalR"]))
    rows.sort(key=lambda x: x[1], reverse=True)
    for name, total, dd, o, i in rows:
        print(f"  {name:20s}  ALL={total:+6.2f}R  DD={dd:4.1f}%   IN={i:+6.2f}  OUT={o:+6.2f}")
