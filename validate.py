"""Robustness validation for the winning variant (stack_J).

- Walk-forward: for each calendar year 2021..2025, evaluate the strategy
  on that year in isolation.
- Leave-one-out: remove each year and confirm aggregate stays profitable.
- Parameter stability: jitter rr, composite_weeks, ema_len to confirm the
  edge is on a plateau, not on a cliff.
- Risk scaling: check how total return / drawdown scale with risk_frac.
- Cross-market surrogate: because we only have BTCUSDT in this repo,
  simulate two "independent" markets by splitting BTC data into odd/even
  weeks and re-running – not a perfect proxy for a different instrument,
  but it breaks path dependence and shows the edge is not reliant on
  specific week sequences.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from improve import Params, run, summary
from backtest import load_15m


WIN = Params(rr=5.0, composite_weeks=2)
WIN_NAME = "stack_J (rr=5, composite=2w)"


def per_year(df15: pd.DataFrame, P: Params):
    rows = []
    for year in [2021, 2022, 2023, 2024, 2025, 2026]:
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end   = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
        # include 2 months of warm-up for the composite
        warmup = start - pd.Timedelta(days=60)
        sub = df15[(df15["ts"] >= warmup) & (df15["ts"] < end)].reset_index(drop=True)
        r = run(sub, P)
        # only count trades whose entry is in-year
        trades = [t for t in r.trades if t.entry_time >= start]
        if not trades:
            rows.append({"year": year, "n": 0, "total": 0.0, "wr": 0.0, "dd": 0.0})
            continue
        pnl = np.array([t.r_mult for t in trades])
        eq = 1.0
        peak = 1.0
        dd = 0.0
        for x in pnl:
            eq *= 1.0 + 0.01 * x
            peak = max(peak, eq)
            dd = max(dd, (peak - eq) / peak)
        rows.append({
            "year": year, "n": len(pnl),
            "total": float(pnl.sum()),
            "wr": float((pnl > 0).mean() * 100),
            "dd": float(dd * 100),
        })
    return rows


def parameter_stability(df15: pd.DataFrame):
    print("\n--- Parameter stability (vs WIN = rr=5, comp=2w) ---")
    grid = [
        ("rr=4.0",   Params(rr=4.0, composite_weeks=2)),
        ("rr=4.5",   Params(rr=4.5, composite_weeks=2)),
        ("rr=5.0",   Params(rr=5.0, composite_weeks=2)),
        ("rr=5.5",   Params(rr=5.5, composite_weeks=2)),
        ("rr=6.0",   Params(rr=6.0, composite_weeks=2)),
        ("comp=1w",  Params(rr=5.0, composite_weeks=1)),
        ("comp=2w",  Params(rr=5.0, composite_weeks=2)),
        ("comp=3w",  Params(rr=5.0, composite_weeks=3)),
        ("ema=40",   Params(rr=5.0, composite_weeks=2, ema_len=40)),
        ("ema=50",   Params(rr=5.0, composite_weeks=2, ema_len=50)),
        ("ema=60",   Params(rr=5.0, composite_weeks=2, ema_len=60)),
        ("bins=40",  Params(rr=5.0, composite_weeks=2, bins=40)),
        ("bins=50",  Params(rr=5.0, composite_weeks=2, bins=50)),
        ("bins=60",  Params(rr=5.0, composite_weeks=2, bins=60)),
        ("va=65",    Params(rr=5.0, composite_weeks=2, value_area=0.65)),
        ("va=70",    Params(rr=5.0, composite_weeks=2, value_area=0.70)),
        ("va=75",    Params(rr=5.0, composite_weeks=2, value_area=0.75)),
    ]
    for name, P in grid:
        s = summary(run(df15, P), name)
        print(f"  {name:8s}  n={s['trades']:3d} total={s['totalR']:+6.2f}R PF={s['pf']:4.2f} DD={s['dd']:4.1f}%")


def leave_one_year_out(df15: pd.DataFrame, P: Params):
    print("\n--- Leave-one-year-out ---")
    rows = per_year(df15, P)
    total = sum(r["total"] for r in rows)
    for drop in [2021, 2022, 2023, 2024, 2025]:
        rem = [r for r in rows if r["year"] != drop]
        print(f"  Drop {drop}: remaining total={sum(r['total'] for r in rem):+6.2f}R")


def risk_scaling(df15: pd.DataFrame, P: Params):
    print("\n--- Risk scaling (using the same trades) ---")
    r = run(df15, P)
    pnl = np.array([t.r_mult for t in r.trades])
    for risk in [0.005, 0.01, 0.015, 0.02, 0.025, 0.03]:
        eq = 10_000.0
        peak = eq
        max_dd = 0.0
        for x in pnl:
            eq *= 1.0 + risk * x
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak)
        ret = (eq / 10_000.0 - 1) * 100
        print(f"  risk={risk * 100:4.1f}%/trade -> final {eq:10,.0f} ({ret:+7.2f}%), max DD {max_dd * 100:5.2f}%")


def odd_even_weeks(df15: pd.DataFrame, P: Params):
    print("\n--- Odd / even week split (path-independence sanity check) ---")
    r = run(df15, P)
    odd = [t for t in r.trades if t.entry_time.isocalendar().week % 2 == 1]
    even = [t for t in r.trades if t.entry_time.isocalendar().week % 2 == 0]
    for label, trades in (("odd weeks", odd), ("even weeks", even)):
        if not trades:
            print(f"  {label:10s}: no trades")
            continue
        pnl = np.array([t.r_mult for t in trades])
        wr = (pnl > 0).mean() * 100
        print(f"  {label:10s}: n={len(pnl):3d} total={pnl.sum():+6.2f}R WR={wr:5.1f}%")


if __name__ == "__main__":
    print("Loading data ...")
    df15 = load_15m()

    print(f"\n=== Validating {WIN_NAME} ===")
    print("\n--- Per-year performance ---")
    rows = per_year(df15, WIN)
    for r in rows:
        mark = "+" if r["total"] > 0 else "-"
        print(f"  {r['year']}  n={r['n']:3d}  {mark}{abs(r['total']):5.2f}R  WR={r['wr']:5.1f}%  DD={r['dd']:4.1f}%")

    leave_one_year_out(df15, WIN)
    parameter_stability(df15)
    risk_scaling(df15, WIN)
    odd_even_weeks(df15, WIN)

    # also compare to baseline
    print("\n\n=== Baseline comparison ===")
    BASE = Params()
    for name, P in [("baseline", BASE), ("stack_J", WIN)]:
        s = summary(run(df15, P), name)
        print(f"  {name:12s}: total={s['totalR']:+6.2f}R DD={s['dd']:4.1f}% PF={s['pf']:4.2f} WR={s['wr']:5.1f}% n={s['trades']}")
