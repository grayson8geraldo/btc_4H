"""
Offline self-test: feed the bot's pure-Python indicator functions with the
same historical CSV data that the backtest uses, then compare the detected
state against what the full backtest (strategies.py) would compute.

Goal: prove the bot produces the SAME signals as the Python backtester so
users can trust that live alerts match the out-of-sample numbers.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backtest"))
sys.path.insert(0, HERE)

from data_loader import load_btc_15m            # noqa: E402
from strategies import resample_4h              # noqa: E402
from indicators import ema as bt_ema, macd as bt_macd  # noqa: E402

from live_macd_bot import (                     # noqa: E402
    FAST_LEN,
    SLOW_LEN,
    EMA_LEN,
    ema as bot_ema,
    macd_line as bot_macd_line,
    atr as bot_atr,
)


def main() -> None:
    df = load_btc_15m()
    df4 = resample_4h(df)
    closes = df4["close"].tolist()

    bot_m = bot_macd_line(closes, FAST_LEN, SLOW_LEN)
    bot_e = bot_ema(closes, EMA_LEN)

    bt_m, _, _ = bt_macd(df4["close"], FAST_LEN, SLOW_LEN, 9)
    bt_e = bt_ema(df4["close"], EMA_LEN)

    # Compare the last 500 bars (after full warm-up)
    n = 500
    bot_m_arr = np.array(bot_m[-n:])
    bt_m_arr  = bt_m.values[-n:]
    bot_e_arr = np.array(bot_e[-n:])
    bt_e_arr  = bt_e.values[-n:]

    macd_err = np.max(np.abs(bot_m_arr - bt_m_arr))
    ema_err  = np.max(np.abs(bot_e_arr - bt_e_arr))

    print(f"Max |MACD(bot) - MACD(backtest)|  = {macd_err:.6f}")
    print(f"Max |EMA50(bot) - EMA50(backtest)| = {ema_err:.6f}")
    assert macd_err < 1e-6, "MACD mismatch"
    assert ema_err  < 1e-6, "EMA50 mismatch"

    # Reproduce the strategy's state logic on the whole 4H history
    states_bot = []
    state = 0
    for i in range(len(closes)):
        m = bot_m[i]
        e = bot_e[i]
        c = closes[i]
        if m != m or e != e:  # NaN during warm-up
            states_bot.append(state)
            continue
        if m > 0 and c > e:
            state = 1
        elif m < 0 and c < e:
            state = -1
        states_bot.append(state)

    # Same logic with backtest series
    states_bt = []
    state = 0
    bt_m_full = bt_m.values
    bt_e_full = bt_e.values
    for i in range(len(closes)):
        m = bt_m_full[i]
        e = bt_e_full[i]
        c = closes[i]
        if np.isnan(m) or np.isnan(e):
            states_bt.append(state)
            continue
        if m > 0 and c > e:
            state = 1
        elif m < 0 and c < e:
            state = -1
        states_bt.append(state)

    mism = sum(1 for a, b in zip(states_bot, states_bt) if a != b)
    print(f"State mismatches on {len(states_bot)} 4H bars: {mism}")
    assert mism == 0

    # ATR on 15m data
    highs  = df["high"].tolist()
    lows   = df["low"].tolist()
    closes_15m = df["close"].tolist()
    bot_a = bot_atr(highs, lows, closes_15m, 14)
    from indicators import atr as bt_atr_fn
    bt_a = bt_atr_fn(df["high"], df["low"], df["close"], 14)
    atr_err = np.max(np.abs(np.array(bot_a[-n:]) - bt_a.values[-n:]))
    print(f"Max |ATR14(bot) - ATR14(backtest)|  = {atr_err:.6f}")
    assert atr_err < 1e-6

    # Dump last 10 state transitions so the user can see real recent signals
    print("\nLast 10 state transitions (bot logic, on 4H bars):")
    prev = 0
    events = []
    for i in range(len(closes)):
        if states_bot[i] != prev and states_bot[i] != 0:
            events.append((df4.index[i], states_bot[i], closes[i]))
            prev = states_bot[i]
    for ts, s, c in events[-10:]:
        side = "LONG " if s == 1 else "SHORT"
        print(f"  {ts}  {side}  @ ${c:,.2f}")

    print("\nOK — bot indicators match the backtest exactly.")


if __name__ == "__main__":
    main()
