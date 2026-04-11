"""
BTC/USDT 15-minute data loader.

Loads all monthly CSV files (Binance klines format) from the repo root and
returns a single sorted pandas DataFrame indexed by UTC datetime.
"""

from __future__ import annotations

import glob
import os
from typing import Optional

import numpy as np
import pandas as pd


BINANCE_COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]


def load_btc_15m(data_dir: Optional[str] = None) -> pd.DataFrame:
    """Load all BTCUSDT-15m-*.csv files into a single DataFrame."""
    if data_dir is None:
        data_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    files = sorted(glob.glob(os.path.join(data_dir, "BTCUSDT-15m-*.csv")))
    if not files:
        raise FileNotFoundError(f"No BTCUSDT 15m files found in {data_dir}")

    frames = []
    for f in files:
        # Some months include a header row, others don't. Peek at the first cell
        # and decide whether to skip the header.
        with open(f, "r") as fh:
            first = fh.readline().split(",", 1)[0]
        skip = 1 if not first.strip().lstrip("-").isdigit() else 0
        df = pd.read_csv(f, header=None, names=BINANCE_COLS, skiprows=skip)
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    df = df.dropna(subset=["open_time"])
    df["open_time"] = df["open_time"].astype(np.int64)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time")

    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("datetime")

    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(np.float64)

    return df[["open", "high", "low", "close", "volume", "quote_volume"]]


def train_test_split(
    df: pd.DataFrame, train_frac: float = 0.70
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological train/test split (no shuffling, no look-ahead)."""
    n = len(df)
    cut = int(n * train_frac)
    train = df.iloc[:cut].copy()
    test = df.iloc[cut:].copy()
    return train, test


if __name__ == "__main__":
    df = load_btc_15m()
    print(f"Rows           : {len(df):,}")
    print(f"Date range     : {df.index[0]}  ->  {df.index[-1]}")
    print(f"Columns        : {list(df.columns)}")
    print(f"First close    : {df['close'].iloc[0]:,.2f}")
    print(f"Last close     : {df['close'].iloc[-1]:,.2f}")
    print(f"Buy&Hold return: {(df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100:.1f}%")

    train, test = train_test_split(df, 0.70)
    print()
    print(f"TRAIN : {train.index[0]} -> {train.index[-1]}  ({len(train):,} bars)")
    print(f"TEST  : {test.index[0]} -> {test.index[-1]}  ({len(test):,} bars)")
