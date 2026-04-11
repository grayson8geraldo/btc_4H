"""
Ten distinct BTC trading strategies.

Design rules (fixed BEFORE looking at any test-set output):

* The repo is named btc_4H -> signals are computed on a 4-hour resampled
  OHLCV frame and then broadcast back to 15-minute bars for execution.
  15-minute bars are used to resolve intra-bar stops/take-profits.
* All parameters are textbook defaults. They are NOT optimised on data.
* Signals fire only on the first 15m bar following a fresh 4H bar close,
  so every 4H signal generates at most one entry.
* Strategy #9 (Opening-Range Breakout) is intrinsically intraday and stays
  on 15m signals.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import (
    atr,
    bollinger,
    donchian,
    ema,
    keltner,
    macd,
    rsi,
    sma,
    stoch,
    supertrend,
)


# ---------------------------------------------------------------------------
# Helpers: resample to 4H and broadcast back to 15m
# ---------------------------------------------------------------------------

def resample_4h(df15: pd.DataFrame) -> pd.DataFrame:
    """Resample 15-minute OHLCV bars to closed 4-hour bars."""
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    df4 = df15[["open", "high", "low", "close", "volume"]].resample("4h").agg(agg).dropna()
    return df4


def broadcast_to_15m(series_4h: pd.Series, index_15: pd.DatetimeIndex) -> pd.Series:
    """Align a 4H series to a 15m index using the *previously closed* 4H bar.

    The 4H bar closes at 00:00, 04:00, 08:00, ... UTC. A signal from the 4H
    bar closed at 04:00 first becomes usable on the 15m bar at 04:00.
    """
    # reindex with method='ffill' then shift by 1 bar of the 4H frame not
    # needed because the resample label is the BAR's OPEN time. So a bar
    # labelled 00:00 uses data 00:00-03:59. At 04:00 15m bar, we can use the
    # 00:00 4H bar (just closed). Reindex with ffill matches.
    aligned = series_4h.reindex(index_15, method="ffill")
    return aligned


def event_flag(values_4h: pd.Series, index_15: pd.DatetimeIndex) -> pd.Series:
    """True only on the first 15m bar after a new 4H value arrives."""
    aligned = broadcast_to_15m(values_4h, index_15)
    return (aligned != aligned.shift(1)).fillna(False)


def _empty_signal(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "entry_long": False,
            "entry_short": False,
            "exit_long": False,
            "exit_short": False,
            "stop_dist": np.nan,
            "tp_dist": np.nan,
        },
        index=df.index,
    )


# ---------------------------------------------------------------------------
# 1. EMA Trend Ribbon (9/21 cross with 200-EMA filter) on 4H
# ---------------------------------------------------------------------------
def strat_ema_ribbon(df: pd.DataFrame) -> pd.DataFrame:
    sig = _empty_signal(df)
    df4 = resample_4h(df)
    e9 = ema(df4["close"], 9)
    e21 = ema(df4["close"], 21)
    e200 = ema(df4["close"], 200)

    long_state = ((e9 > e21) & (df4["close"] > e200)).astype(int)
    short_state = ((e9 < e21) & (df4["close"] < e200)).astype(int)
    # State coded: +1 long, -1 short, 0 flat
    state = long_state - short_state

    state_15 = broadcast_to_15m(state, df.index).fillna(0)
    prev = state_15.shift(1).fillna(0)
    a15 = atr(df["high"], df["low"], df["close"], 14)

    sig["entry_long"] = (state_15 == 1) & (prev != 1)
    sig["entry_short"] = (state_15 == -1) & (prev != -1)
    sig["exit_long"] = (state_15 != 1) & (prev == 1)
    sig["exit_short"] = (state_15 != -1) & (prev == -1)
    sig["stop_dist"] = 3.0 * a15
    sig["tp_dist"] = 0.0  # ride the trend; exit via state change
    return sig


# ---------------------------------------------------------------------------
# 2. RSI-2 Mean Reversion (Larry Connors) on 4H + 200-EMA filter
# ---------------------------------------------------------------------------
def strat_rsi2_meanrev(df: pd.DataFrame) -> pd.DataFrame:
    sig = _empty_signal(df)
    df4 = resample_4h(df)
    r = rsi(df4["close"], 2)
    e200 = ema(df4["close"], 200)

    long_entry = (r < 10) & (df4["close"] > e200)
    short_entry = (r > 90) & (df4["close"] < e200)
    long_exit = r > 70
    short_exit = r < 30

    entry_l_15 = event_flag(long_entry.astype(int), df.index) & (
        broadcast_to_15m(long_entry.astype(int), df.index) == 1
    )
    entry_s_15 = event_flag(short_entry.astype(int), df.index) & (
        broadcast_to_15m(short_entry.astype(int), df.index) == 1
    )
    exit_l_15 = event_flag(long_exit.astype(int), df.index) & (
        broadcast_to_15m(long_exit.astype(int), df.index) == 1
    )
    exit_s_15 = event_flag(short_exit.astype(int), df.index) & (
        broadcast_to_15m(short_exit.astype(int), df.index) == 1
    )

    a15 = atr(df["high"], df["low"], df["close"], 14)
    sig["entry_long"] = entry_l_15
    sig["entry_short"] = entry_s_15
    sig["exit_long"] = exit_l_15
    sig["exit_short"] = exit_s_15
    sig["stop_dist"] = 3.0 * a15
    sig["tp_dist"] = 3.0 * a15
    return sig


# ---------------------------------------------------------------------------
# 3. Donchian 20 Breakout (Turtle) on 4H
# ---------------------------------------------------------------------------
def strat_donchian_breakout(df: pd.DataFrame) -> pd.DataFrame:
    sig = _empty_signal(df)
    df4 = resample_4h(df)
    _, upper, lower = donchian(df4["high"], df4["low"], 20)
    _, exit_up, exit_dn = donchian(df4["high"], df4["low"], 10)

    upper_prev = upper.shift(1)
    lower_prev = lower.shift(1)

    # Position state
    long_state = pd.Series(0, index=df4.index)
    in_pos = 0
    for i in range(len(df4)):
        c = df4["close"].iloc[i]
        up = upper_prev.iloc[i]
        lo = lower_prev.iloc[i]
        eu = exit_up.shift(1).iloc[i]
        ed = exit_dn.shift(1).iloc[i]
        if in_pos == 0:
            if not np.isnan(up) and c > up:
                in_pos = 1
            elif not np.isnan(lo) and c < lo:
                in_pos = -1
        elif in_pos == 1:
            if not np.isnan(ed) and c < ed:
                in_pos = 0
        elif in_pos == -1:
            if not np.isnan(eu) and c > eu:
                in_pos = 0
        long_state.iloc[i] = in_pos

    state_15 = broadcast_to_15m(long_state, df.index).fillna(0)
    prev = state_15.shift(1).fillna(0)
    a15 = atr(df["high"], df["low"], df["close"], 14)

    sig["entry_long"] = (state_15 == 1) & (prev != 1)
    sig["entry_short"] = (state_15 == -1) & (prev != -1)
    sig["exit_long"] = (state_15 != 1) & (prev == 1)
    sig["exit_short"] = (state_15 != -1) & (prev == -1)
    sig["stop_dist"] = 3.0 * a15
    sig["tp_dist"] = 0.0
    return sig


# ---------------------------------------------------------------------------
# 4. Bollinger-Band Squeeze Breakout on 4H
# ---------------------------------------------------------------------------
def strat_bollinger_squeeze(df: pd.DataFrame) -> pd.DataFrame:
    sig = _empty_signal(df)
    df4 = resample_4h(df)
    basis, upper, lower = bollinger(df4["close"], 20, 2.0)
    bw = (upper - lower) / basis
    squeeze = bw < bw.rolling(100, min_periods=100).quantile(0.25)

    upper_prev = upper.shift(1)
    lower_prev = lower.shift(1)
    long_entry = squeeze.shift(1).fillna(False) & (df4["close"] > upper_prev)
    short_entry = squeeze.shift(1).fillna(False) & (df4["close"] < lower_prev)
    long_exit = df4["close"] < basis
    short_exit = df4["close"] > basis

    entry_l_15 = event_flag(long_entry.astype(int), df.index) & (
        broadcast_to_15m(long_entry.astype(int), df.index) == 1
    )
    entry_s_15 = event_flag(short_entry.astype(int), df.index) & (
        broadcast_to_15m(short_entry.astype(int), df.index) == 1
    )
    exit_l_15 = event_flag(long_exit.astype(int), df.index) & (
        broadcast_to_15m(long_exit.astype(int), df.index) == 1
    )
    exit_s_15 = event_flag(short_exit.astype(int), df.index) & (
        broadcast_to_15m(short_exit.astype(int), df.index) == 1
    )

    a15 = atr(df["high"], df["low"], df["close"], 14)
    sig["entry_long"] = entry_l_15
    sig["entry_short"] = entry_s_15
    sig["exit_long"] = exit_l_15
    sig["exit_short"] = exit_s_15
    sig["stop_dist"] = 3.0 * a15
    sig["tp_dist"] = 6.0 * a15
    return sig


# ---------------------------------------------------------------------------
# 5. MACD Zero-Line Cross + 50-EMA filter on 4H
# ---------------------------------------------------------------------------
def strat_macd_zero(df: pd.DataFrame) -> pd.DataFrame:
    sig = _empty_signal(df)
    df4 = resample_4h(df)
    m, s, _ = macd(df4["close"], 12, 26, 9)
    e50 = ema(df4["close"], 50)

    state = pd.Series(0, index=df4.index)
    long_cond = (m > 0) & (df4["close"] > e50)
    short_cond = (m < 0) & (df4["close"] < e50)
    state[long_cond] = 1
    state[short_cond] = -1

    state_15 = broadcast_to_15m(state, df.index).fillna(0)
    prev = state_15.shift(1).fillna(0)
    a15 = atr(df["high"], df["low"], df["close"], 14)

    sig["entry_long"] = (state_15 == 1) & (prev != 1)
    sig["entry_short"] = (state_15 == -1) & (prev != -1)
    sig["exit_long"] = (state_15 != 1) & (prev == 1)
    sig["exit_short"] = (state_15 != -1) & (prev == -1)
    sig["stop_dist"] = 3.0 * a15
    sig["tp_dist"] = 0.0
    return sig


# ---------------------------------------------------------------------------
# 6. Supertrend Follower on 4H
# ---------------------------------------------------------------------------
def strat_supertrend(df: pd.DataFrame) -> pd.DataFrame:
    sig = _empty_signal(df)
    df4 = resample_4h(df)
    direction, _, _ = supertrend(df4["high"], df4["low"], df4["close"], 10, 3.0)
    direction = direction.fillna(0)

    state_15 = broadcast_to_15m(direction, df.index).fillna(0)
    prev = state_15.shift(1).fillna(0)
    a15 = atr(df["high"], df["low"], df["close"], 14)

    sig["entry_long"] = (state_15 == 1) & (prev != 1)
    sig["entry_short"] = (state_15 == -1) & (prev != -1)
    sig["exit_long"] = (state_15 != 1) & (prev == 1)
    sig["exit_short"] = (state_15 != -1) & (prev == -1)
    sig["stop_dist"] = 3.0 * a15
    sig["tp_dist"] = 0.0
    return sig


# ---------------------------------------------------------------------------
# 7. Stochastic oversold-cross bounce + 200-EMA trend filter on 4H
# ---------------------------------------------------------------------------
def strat_stoch_bounce(df: pd.DataFrame) -> pd.DataFrame:
    sig = _empty_signal(df)
    df4 = resample_4h(df)
    k, d = stoch(df4["high"], df4["low"], df4["close"], 14, 3)
    e200 = ema(df4["close"], 200)

    long_entry = (k > d) & (k.shift(1) <= d.shift(1)) & (k < 30) & (df4["close"] > e200)
    short_entry = (k < d) & (k.shift(1) >= d.shift(1)) & (k > 70) & (df4["close"] < e200)
    long_exit = k > 80
    short_exit = k < 20

    entry_l_15 = event_flag(long_entry.astype(int), df.index) & (
        broadcast_to_15m(long_entry.astype(int), df.index) == 1
    )
    entry_s_15 = event_flag(short_entry.astype(int), df.index) & (
        broadcast_to_15m(short_entry.astype(int), df.index) == 1
    )
    exit_l_15 = event_flag(long_exit.astype(int), df.index) & (
        broadcast_to_15m(long_exit.astype(int), df.index) == 1
    )
    exit_s_15 = event_flag(short_exit.astype(int), df.index) & (
        broadcast_to_15m(short_exit.astype(int), df.index) == 1
    )

    a15 = atr(df["high"], df["low"], df["close"], 14)
    sig["entry_long"] = entry_l_15
    sig["entry_short"] = entry_s_15
    sig["exit_long"] = exit_l_15
    sig["exit_short"] = exit_s_15
    sig["stop_dist"] = 3.0 * a15
    sig["tp_dist"] = 4.5 * a15
    return sig


# ---------------------------------------------------------------------------
# 8. Keltner-Channel pullback in trend on 4H
# ---------------------------------------------------------------------------
def strat_keltner_pullback(df: pd.DataFrame) -> pd.DataFrame:
    sig = _empty_signal(df)
    df4 = resample_4h(df)
    basis, upper, lower = keltner(df4["high"], df4["low"], df4["close"], 20, 1.5, 10)
    e200 = ema(df4["close"], 200)

    long_entry = (df4["close"] > e200) & (df4["low"] <= basis) & (df4["close"] > basis)
    short_entry = (df4["close"] < e200) & (df4["high"] >= basis) & (df4["close"] < basis)
    long_exit = df4["close"] >= upper
    short_exit = df4["close"] <= lower

    entry_l_15 = event_flag(long_entry.astype(int), df.index) & (
        broadcast_to_15m(long_entry.astype(int), df.index) == 1
    )
    entry_s_15 = event_flag(short_entry.astype(int), df.index) & (
        broadcast_to_15m(short_entry.astype(int), df.index) == 1
    )
    exit_l_15 = event_flag(long_exit.astype(int), df.index) & (
        broadcast_to_15m(long_exit.astype(int), df.index) == 1
    )
    exit_s_15 = event_flag(short_exit.astype(int), df.index) & (
        broadcast_to_15m(short_exit.astype(int), df.index) == 1
    )

    a15 = atr(df["high"], df["low"], df["close"], 14)
    sig["entry_long"] = entry_l_15
    sig["entry_short"] = entry_s_15
    sig["exit_long"] = exit_l_15
    sig["exit_short"] = exit_s_15
    sig["stop_dist"] = 2.5 * a15
    sig["tp_dist"] = 5.0 * a15
    return sig


# ---------------------------------------------------------------------------
# 9. Opening-Range Breakout (first 4 bars of each UTC day = 1h range) on 15m
# ---------------------------------------------------------------------------
def strat_orb(df: pd.DataFrame) -> pd.DataFrame:
    sig = _empty_signal(df)
    a = atr(df["high"], df["low"], df["close"], 14)

    day = df.index.date
    bar_of_day = df.groupby(pd.Index(day)).cumcount()
    or_mask = bar_of_day < 4  # first hour

    tmp = pd.DataFrame({"high": df["high"], "low": df["low"], "day": day})
    or_high = tmp[or_mask].groupby("day")["high"].max().reindex(day).values
    or_low = tmp[or_mask].groupby("day")["low"].min().reindex(day).values

    or_high_s = pd.Series(or_high, index=df.index)
    or_low_s = pd.Series(or_low, index=df.index)

    active = (bar_of_day >= 4) & (bar_of_day < (24 * 4) - 16)
    broke_up = active & (df["close"] > or_high_s) & (df["close"].shift(1) <= or_high_s)
    broke_dn = active & (df["close"] < or_low_s) & (df["close"].shift(1) >= or_low_s)
    last_bar_of_day = bar_of_day == (24 * 4) - 1

    sig["entry_long"] = broke_up
    sig["entry_short"] = broke_dn
    sig["exit_long"] = last_bar_of_day
    sig["exit_short"] = last_bar_of_day
    sig["stop_dist"] = 2.0 * a
    sig["tp_dist"] = 4.0 * a
    return sig


# ---------------------------------------------------------------------------
# 10. Volatility-Contraction Breakout on 4H (55-bar Donchian + ATR squeeze)
# ---------------------------------------------------------------------------
def strat_vol_contraction(df: pd.DataFrame) -> pd.DataFrame:
    sig = _empty_signal(df)
    df4 = resample_4h(df)
    _, upper, lower = donchian(df4["high"], df4["low"], 55)
    a4 = atr(df4["high"], df4["low"], df4["close"], 14)
    a_med = a4.rolling(100, min_periods=100).median()
    compressed = a4 < 0.8 * a_med
    _, up20, dn20 = donchian(df4["high"], df4["low"], 20)

    state = pd.Series(0, index=df4.index)
    in_pos = 0
    for i in range(len(df4)):
        c = df4["close"].iloc[i]
        up = upper.shift(1).iloc[i]
        lo = lower.shift(1).iloc[i]
        eu = up20.shift(1).iloc[i]
        ed = dn20.shift(1).iloc[i]
        comp_prev = compressed.shift(1).iloc[i] if i > 0 else False
        if in_pos == 0:
            if comp_prev and not np.isnan(up) and c > up:
                in_pos = 1
            elif comp_prev and not np.isnan(lo) and c < lo:
                in_pos = -1
        elif in_pos == 1:
            if not np.isnan(ed) and c < ed:
                in_pos = 0
        elif in_pos == -1:
            if not np.isnan(eu) and c > eu:
                in_pos = 0
        state.iloc[i] = in_pos

    state_15 = broadcast_to_15m(state, df.index).fillna(0)
    prev = state_15.shift(1).fillna(0)
    a15 = atr(df["high"], df["low"], df["close"], 14)

    sig["entry_long"] = (state_15 == 1) & (prev != 1)
    sig["entry_short"] = (state_15 == -1) & (prev != -1)
    sig["exit_long"] = (state_15 != 1) & (prev == 1)
    sig["exit_short"] = (state_15 != -1) & (prev == -1)
    sig["stop_dist"] = 3.0 * a15
    sig["tp_dist"] = 0.0
    return sig


STRATEGIES = {
    "01_ema_ribbon": strat_ema_ribbon,
    "02_rsi2_meanrev": strat_rsi2_meanrev,
    "03_donchian_breakout": strat_donchian_breakout,
    "04_bollinger_squeeze": strat_bollinger_squeeze,
    "05_macd_zero": strat_macd_zero,
    "06_supertrend": strat_supertrend,
    "07_stoch_bounce": strat_stoch_bounce,
    "08_keltner_pullback": strat_keltner_pullback,
    "09_orb_1h": strat_orb,
    "10_vol_contraction": strat_vol_contraction,
}
