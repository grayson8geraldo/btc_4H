"""
Vectorized technical indicators used by strategies.

Kept deliberately small and self-contained so that the exact same formulas
can be transcribed into Pine Script for the TradingView indicators.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(x: pd.Series, n: int) -> pd.Series:
    # Wilder's smoothing used by RSI / ATR in Pine Script (ta.rma)
    return x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = rma(gain, n)
    avg_loss = rma(loss, n)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return rma(tr, n)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    basis = sma(close, n)
    dev = close.rolling(n, min_periods=n).std(ddof=0)
    upper = basis + k * dev
    lower = basis - k * dev
    return basis, upper, lower


def donchian(high: pd.Series, low: pd.Series, n: int = 20):
    upper = high.rolling(n, min_periods=n).max()
    lower = low.rolling(n, min_periods=n).min()
    basis = (upper + lower) / 2
    return basis, upper, lower


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(close, fast) - ema(close, slow)
    sig_line = ema(macd_line, signal)
    hist = macd_line - sig_line
    return macd_line, sig_line, hist


def stoch(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14, d: int = 3):
    hh = high.rolling(n, min_periods=n).max()
    ll = low.rolling(n, min_periods=n).min()
    k = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    k = k.fillna(50.0)
    d_line = sma(k, d)
    return k, d_line


def supertrend(
    high: pd.Series, low: pd.Series, close: pd.Series, n: int = 10, mult: float = 3.0
):
    """Classic Supertrend (returns direction: +1 uptrend / -1 downtrend)."""
    atr_ = atr(high, low, close, n)
    hl2 = (high + low) / 2
    upper_basic = hl2 + mult * atr_
    lower_basic = hl2 - mult * atr_

    upper = upper_basic.copy()
    lower = lower_basic.copy()
    direction = pd.Series(index=close.index, dtype="float64")
    trend = 1

    u_prev = np.nan
    l_prev = np.nan
    c_prev = np.nan

    for i in range(len(close)):
        ub = upper_basic.iloc[i]
        lb = lower_basic.iloc[i]
        if np.isnan(ub) or np.isnan(lb):
            direction.iloc[i] = np.nan
            u_prev = ub
            l_prev = lb
            c_prev = close.iloc[i]
            continue

        if not np.isnan(u_prev):
            ub = min(ub, u_prev) if c_prev <= u_prev else ub
        if not np.isnan(l_prev):
            lb = max(lb, l_prev) if c_prev >= l_prev else lb

        if close.iloc[i] > (u_prev if not np.isnan(u_prev) else ub):
            trend = 1
        elif close.iloc[i] < (l_prev if not np.isnan(l_prev) else lb):
            trend = -1

        direction.iloc[i] = trend
        upper.iloc[i] = ub
        lower.iloc[i] = lb
        u_prev = ub
        l_prev = lb
        c_prev = close.iloc[i]

    return direction, upper, lower


def keltner(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    n: int = 20,
    mult: float = 1.5,
    atr_n: int = 10,
):
    basis = ema(close, n)
    rng = atr(high, low, close, atr_n)
    upper = basis + mult * rng
    lower = basis - mult * rng
    return basis, upper, lower
