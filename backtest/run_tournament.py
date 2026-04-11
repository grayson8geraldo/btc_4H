"""
Run all 10 strategies on TRAIN and TEST data and produce a tournament report.

TRAIN  = first 70% of the data (Jan 2021 -> Jul 2024)  -> sanity check
TEST   = final 30%  (Jul 2024 -> Mar 2026)             -> honest out-of-sample

The test period (~20 months) is used exclusively as the final verdict. No
parameters are tuned on it. Every strategy uses fixed textbook defaults
declared inside strategies.py.
"""

from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_loader import load_btc_15m, train_test_split
from engine import run_backtest, START_EQUITY
from strategies import STRATEGIES


def score(row: dict) -> float:
    """Composite ranking score.

    Favours high returns, penalises heavy drawdowns and low profit factors.
    The formula is declared BEFORE looking at any numbers; it is the same on
    train and test.
    """
    ret = row["return_pct"]
    dd = abs(row["max_dd_pct"])
    pf = row["profit_factor"] if row["profit_factor"] != float("inf") else 5.0
    trades = row["trades"]
    if trades < 20:
        return -1e9  # not enough samples -> disqualified
    # shrink ridiculous profit factor outliers
    pf = min(pf, 5.0)
    # calmar-like, rewards compounding and penalises drawdowns
    calmar = ret / dd if dd > 0 else ret
    return 0.5 * calmar + 0.3 * row["sortino"] + 0.2 * (pf - 1.0) * 10


def fmt_row(r: dict) -> str:
    return (
        f"{r['strategy']:<22} "
        f"${r['final_equity']:>8,.2f}  "
        f"{r['return_pct']:>+8.1f}%  "
        f"DD {r['max_dd_pct']:>6.1f}%  "
        f"trades {r['trades']:>4}  "
        f"win {r['win_rate_pct']:>5.1f}%  "
        f"PF {r['profit_factor']:>5.2f}  "
        f"Sortino {r['sortino']:>6.2f}"
    )


def main() -> None:
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
    os.makedirs(out_dir, exist_ok=True)

    df = load_btc_15m()
    train, test = train_test_split(df, 0.70)

    print("=" * 100)
    print(f"BTC 15m bars: {len(df):,}")
    print(f"TRAIN: {train.index[0]} -> {train.index[-1]}  ({len(train):,} bars)")
    print(f"TEST : {test.index[0]} -> {test.index[-1]}  ({len(test):,} bars)")
    print(f"Starting equity: ${START_EQUITY:.2f}")
    print("=" * 100)

    train_rows = []
    test_rows = []

    for name, fn in STRATEGIES.items():
        print(f"\n-> {name}")
        r_train = run_backtest(train, fn, name)
        r_test = run_backtest(test, fn, name)
        train_rows.append(r_train.summary_row())
        test_rows.append(r_test.summary_row())
        print("   TRAIN :", fmt_row(r_train.summary_row()))
        print("   TEST  :", fmt_row(r_test.summary_row()))

    train_df = pd.DataFrame(train_rows)
    test_df = pd.DataFrame(test_rows)

    train_df["score"] = train_df.apply(lambda r: score(r.to_dict()), axis=1)
    test_df["score"] = test_df.apply(lambda r: score(r.to_dict()), axis=1)

    train_df = train_df.sort_values("score", ascending=False).reset_index(drop=True)
    test_df_sorted = test_df.sort_values("score", ascending=False).reset_index(drop=True)

    print("\n" + "=" * 100)
    print("TRAIN-set ranking (2021-01 -> 2024-07) — sanity check only")
    print("=" * 100)
    print(train_df.to_string(index=False))

    print("\n" + "=" * 100)
    print("*** TEST-set ranking (2024-07 -> 2026-03) — OUT-OF-SAMPLE, the verdict ***")
    print("=" * 100)
    print(test_df_sorted.to_string(index=False))

    winner = test_df_sorted.iloc[0]
    print("\n" + "=" * 100)
    print(f"WINNER (out-of-sample): {winner['strategy']}")
    print(
        f"  start $200 -> ${winner['final_equity']:.2f}   "
        f"return {winner['return_pct']:+.1f}%   "
        f"max DD {winner['max_dd_pct']:.1f}%   "
        f"PF {winner['profit_factor']:.2f}   "
        f"Sortino {winner['sortino']:.2f}"
    )
    print("=" * 100)

    train_df.to_csv(os.path.join(out_dir, "train_results.csv"), index=False)
    test_df_sorted.to_csv(os.path.join(out_dir, "test_results.csv"), index=False)

    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump(
            {
                "start_equity": START_EQUITY,
                "train_period": [str(train.index[0]), str(train.index[-1])],
                "test_period": [str(test.index[0]), str(test.index[-1])],
                "train_table": train_df.to_dict(orient="records"),
                "test_table": test_df_sorted.to_dict(orient="records"),
                "winner": winner.to_dict(),
            },
            f,
            indent=2,
            default=str,
        )


if __name__ == "__main__":
    main()
