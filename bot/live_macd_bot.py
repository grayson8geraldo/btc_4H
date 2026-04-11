"""
Live Telegram bot for BTC Tournament Strategy #5 — MACD Zero 4H (WINNER).

The bot replicates the exact state logic from backtest/strategies.py
(strat_macd_zero): on every run it pulls fresh 4-hour candles from the
public Binance API, computes MACD(12,26,9) + EMA(50), and — when the
state (long / short / neutral) changes — sends a Telegram message with:

    * direction (LONG / SHORT / FLAT)
    * entry price (current market)
    * stop-loss price (entry ± 3 * ATR14 of 15-minute data)
    * position size (risk-based, configurable)
    * leverage check (capped at 3x)

Runs on pure Python stdlib — no pip install required. Works on any host
with Python 3.8+ and outbound HTTPS.

--------------------------------------------------------------------------
Quick start:

    export TELEGRAM_BOT_TOKEN="123456:AAABBBCCC"   # from @BotFather
    export TELEGRAM_CHAT_ID="987654321"            # your user id from @userinfobot
    python3 bot/live_macd_bot.py --once            # fire once, useful for cron
    python3 bot/live_macd_bot.py --loop 900        # run forever, check every 15 min
    python3 bot/live_macd_bot.py --test            # send a test message only
    python3 bot/live_macd_bot.py --status          # print current state (no alert)

Cron job (Linux):  every 15 minutes, 30 seconds after each quarter-hour

    */15 * * * *  sleep 30 && cd /path/to/btc_4H && python3 bot/live_macd_bot.py --once >> bot/bot.log 2>&1
--------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone


# ----- Strategy parameters (same as strat_macd_zero) -----------------------
SYMBOL       = "BTCUSDT"
HTF_INTERVAL = "4h"
LTF_INTERVAL = "15m"

FAST_LEN   = 12
SLOW_LEN   = 26
SIGNAL_LEN = 9
EMA_LEN    = 50
ATR_LEN    = 14
STOP_MULT  = 3.0

# ----- Risk model (same defaults as the backtest) --------------------------
DEFAULT_EQUITY   = float(os.environ.get("BOT_EQUITY", "200"))
RISK_PER_TRADE   = float(os.environ.get("BOT_RISK_PCT", "0.01"))   # 1%
MAX_LEVERAGE     = float(os.environ.get("BOT_MAX_LEV",  "3.0"))

# ----- IO ------------------------------------------------------------------
BINANCE_API = "https://api.binance.com/api/v3/klines"
STATE_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_state.json")
LOG_PREFIX  = "[live_macd_bot]"

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT  = os.environ.get("TELEGRAM_CHAT_ID",   "").strip()


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

def fetch_klines(interval: str, limit: int) -> list[dict]:
    url = f"{BINANCE_API}?symbol={SYMBOL}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "btc-tournament-bot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = json.loads(r.read())
    now_ms = int(time.time() * 1000)
    bars: list[dict] = []
    for row in raw:
        bars.append(
            {
                "open_time": int(row[0]),
                "open":  float(row[1]),
                "high":  float(row[2]),
                "low":   float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": int(row[6]),
                "closed": int(row[6]) < now_ms,
            }
        )
    return bars


# ---------------------------------------------------------------------------
# Indicators (pure Python, match indicators.py from the backtest)
# ---------------------------------------------------------------------------

def ema(values: list[float], n: int) -> list[float]:
    out: list[float] = [float("nan")] * len(values)
    if len(values) < n:
        return out
    k = 2.0 / (n + 1)
    seed = sum(values[:n]) / n
    out[n - 1] = seed
    for i in range(n, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def rma(values: list[float], n: int) -> list[float]:
    """Wilder's smoothing — matches Pine's ta.rma and pandas ewm(alpha=1/n)."""
    out: list[float] = [float("nan")] * len(values)
    if len(values) < n:
        return out
    seed = sum(values[:n]) / n
    out[n - 1] = seed
    alpha = 1.0 / n
    for i in range(n, len(values)):
        out[i] = values[i] * alpha + out[i - 1] * (1 - alpha)
    return out


def macd_line(closes: list[float], fast: int, slow: int) -> list[float]:
    ef = ema(closes, fast)
    es = ema(closes, slow)
    out = [float("nan")] * len(closes)
    for i in range(len(closes)):
        if ef[i] == ef[i] and es[i] == es[i]:   # not NaN
            out[i] = ef[i] - es[i]
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], n: int) -> list[float]:
    trs: list[float] = []
    prev_close = closes[0]
    for i, (h, l, c) in enumerate(zip(highs, lows, closes)):
        if i == 0:
            trs.append(h - l)
        else:
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
            trs.append(tr)
        prev_close = c
    return rma(trs, n)


# ---------------------------------------------------------------------------
# State logic — identical to strat_macd_zero in backtest/strategies.py
# ---------------------------------------------------------------------------

def compute_state_4h(prev_state: int) -> tuple[int, float, int, float, float]:
    """Return (new_state, close, open_time, macd_val, ema_val).

    Uses the LAST CLOSED 4H bar only, so the function is deterministic and
    never looks into a partially-formed candle.
    """
    bars = fetch_klines(HTF_INTERVAL, 300)
    if not bars[-1]["closed"]:
        bars = bars[:-1]
    closes = [b["close"] for b in bars]

    m_line = macd_line(closes, FAST_LEN, SLOW_LEN)
    e50    = ema(closes, EMA_LEN)

    last_close = closes[-1]
    last_macd  = m_line[-1]
    last_ema50 = e50[-1]

    long_cond  = last_macd > 0 and last_close > last_ema50
    short_cond = last_macd < 0 and last_close < last_ema50

    if long_cond:
        new_state = 1
    elif short_cond:
        new_state = -1
    else:
        new_state = prev_state   # Pine rule: state is preserved when neither fires

    return new_state, last_close, bars[-1]["open_time"], last_macd, last_ema50


def compute_stop_distance_15m() -> float:
    bars = fetch_klines(LTF_INTERVAL, 200)
    if not bars[-1]["closed"]:
        bars = bars[:-1]
    highs  = [b["high"]  for b in bars]
    lows   = [b["low"]   for b in bars]
    closes = [b["close"] for b in bars]
    a = atr(highs, lows, closes, ATR_LEN)
    return a[-1] * STOP_MULT


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print(f"{LOG_PREFIX} [WARN] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — printing instead:\n{text}")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    body = urllib.parse.urlencode(
        {
            "chat_id": TG_CHAT,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": "true",
        }
    ).encode()
    req = urllib.request.Request(url, data=body)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
    except Exception as e:  # noqa: BLE001
        print(f"{LOG_PREFIX} [ERROR] Telegram send failed: {e}")


def format_signal(
    direction: int,
    entry: float,
    stop_dist: float,
    equity: float,
    macd_val: float,
    ema_val: float,
) -> str:
    if direction == 1:
        side_emoji = "🟢 *LONG*"
        stop_px = entry - stop_dist
        stop_sign = "−"
    else:
        side_emoji = "🔴 *SHORT*"
        stop_px = entry + stop_dist
        stop_sign = "+"

    risk_usd = equity * RISK_PER_TRADE
    size_btc = risk_usd / stop_dist
    notional = size_btc * entry
    if notional > equity * MAX_LEVERAGE:
        size_btc = (equity * MAX_LEVERAGE) / entry
        notional = size_btc * entry
    leverage = notional / equity

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return (
        f"*BTC Tournament #5 — MACD Zero 4H*\n"
        f"{side_emoji}  `BTCUSDT`\n"
        f"\n"
        f"`Entry      ` ~ ${entry:,.2f}\n"
        f"`Stop-loss  ` $ {stop_px:,.2f}  ({stop_sign}${stop_dist:,.2f})\n"
        f"`Take-profit` none — exit on opposite / flat signal\n"
        f"\n"
        f"*Risk model (equity ${equity:,.2f}):*\n"
        f"`  risk   ` {RISK_PER_TRADE * 100:.1f}% = ${risk_usd:.2f}\n"
        f"`  size   ` {size_btc:.5f} BTC\n"
        f"`  notion.` ${notional:,.2f}\n"
        f"`  lev    ` {leverage:.2f}x  (cap {MAX_LEVERAGE:.0f}x)\n"
        f"\n"
        f"*4H context:*\n"
        f"`  MACD   ` {macd_val:+.2f}\n"
        f"`  EMA50  ` ${ema_val:,.2f}\n"
        f"\n"
        f"_Signal fired {now}._\n"
        f"_Execute market order now; close on next opposite/flat alert._"
    )


def format_flat(entry: float) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"*BTC Tournament #5 — MACD Zero 4H*\n"
        f"⚪ *FLAT*  `BTCUSDT`\n\n"
        f"State cleared. Close any open position at ~${entry:,.2f} and wait for the next LONG/SHORT signal.\n\n"
        f"_{now}_"
    )


# ---------------------------------------------------------------------------
# Persistent state
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    return {"state": 0, "last_bar_time": 0, "last_alert_time": 0}


def save_state(s: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_once(equity: float, silent_first_run: bool = True) -> None:
    state_data = load_state()
    prev_state = state_data.get("state", 0)
    is_first_run = state_data.get("last_bar_time", 0) == 0

    try:
        new_state, close_px, bar_time, macd_v, ema_v = compute_state_4h(prev_state)
    except Exception as e:  # noqa: BLE001
        print(f"{LOG_PREFIX} [ERROR] data fetch failed: {e}")
        sys.exit(1)

    # Only act once per 4H bar
    if bar_time == state_data.get("last_bar_time", 0) and new_state == prev_state:
        print(f"{LOG_PREFIX} {_ts()}  no change, state={_s(new_state)}, close=${close_px:,.2f}")
        return

    changed = new_state != prev_state

    if changed:
        if is_first_run and silent_first_run:
            print(f"{LOG_PREFIX} [first run] state={_s(new_state)}, no alert sent, state persisted")
        else:
            if new_state == 0:
                send_telegram(format_flat(close_px))
            else:
                try:
                    stop_dist = compute_stop_distance_15m()
                except Exception as e:  # noqa: BLE001
                    print(f"{LOG_PREFIX} [ERROR] stop distance calc failed: {e}")
                    sys.exit(1)
                send_telegram(format_signal(new_state, close_px, stop_dist, equity, macd_v, ema_v))
            state_data["last_alert_time"] = int(time.time())
        state_data["state"] = new_state

    state_data["last_bar_time"] = bar_time
    save_state(state_data)
    print(f"{LOG_PREFIX} {_ts()}  state={_s(new_state)} (was {_s(prev_state)}), close=${close_px:,.2f}, macd={macd_v:+.2f}")


def cmd_loop(interval: int, equity: float) -> None:
    print(f"{LOG_PREFIX} starting loop, interval={interval}s, equity=${equity}")
    while True:
        try:
            cmd_once(equity, silent_first_run=True)
        except SystemExit:
            pass
        except Exception as e:  # noqa: BLE001
            print(f"{LOG_PREFIX} [ERROR] loop iteration failed: {e}")
        time.sleep(interval)


def cmd_test() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    send_telegram(f"*BTC Tournament #5 — test message*\nBot is alive. {now}")
    print(f"{LOG_PREFIX} test message sent")


def cmd_status(equity: float) -> None:
    state_data = load_state()
    prev_state = state_data.get("state", 0)
    try:
        new_state, close_px, bar_time, macd_v, ema_v = compute_state_4h(prev_state)
        stop_dist = compute_stop_distance_15m()
    except Exception as e:  # noqa: BLE001
        print(f"{LOG_PREFIX} [ERROR] {e}")
        sys.exit(1)

    print(f"{LOG_PREFIX} {_ts()}")
    print(f"  state (stored) : {_s(prev_state)}")
    print(f"  state (computed): {_s(new_state)}")
    print(f"  4H close        : ${close_px:,.2f}")
    print(f"  4H MACD         : {macd_v:+.2f}")
    print(f"  4H EMA50        : ${ema_v:,.2f}")
    print(f"  15m stop dist   : ${stop_dist:,.2f}")
    if new_state != 0:
        if new_state == 1:
            print(f"  current long stop   -> ${close_px - stop_dist:,.2f}")
        else:
            print(f"  current short stop  -> ${close_px + stop_dist:,.2f}")


def _s(state: int) -> str:
    return {1: "LONG", -1: "SHORT", 0: "FLAT"}.get(state, "?")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="BTC Tournament #5 live Telegram bot")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once",   action="store_true", help="Run one check and exit (use with cron)")
    mode.add_argument("--loop",   type=int, metavar="SEC", help="Run forever, sleep SEC seconds between checks")
    mode.add_argument("--test",   action="store_true", help="Send a Telegram test message and exit")
    mode.add_argument("--status", action="store_true", help="Print current state without sending alerts")
    p.add_argument("--equity", type=float, default=DEFAULT_EQUITY,
                   help=f"Account equity for position sizing (default ${DEFAULT_EQUITY})")
    args = p.parse_args()

    if args.test:
        cmd_test()
    elif args.once:
        cmd_once(args.equity)
    elif args.loop is not None:
        cmd_loop(args.loop, args.equity)
    elif args.status:
        cmd_status(args.equity)


if __name__ == "__main__":
    main()
