"""
Honest event-driven backtester for intraday BTC strategies.

Rules for honest backtesting that the engine enforces:

1. Signals are evaluated on bar (i); trades execute on the OPEN of bar (i+1).
   This eliminates all look-ahead on the signal itself.
2. Intrabar stop-loss / take-profit use the HIGH and LOW of the *next* bars
   only (never the same bar a position is opened on the close of).
3. Trading fees and slippage are applied to every entry and exit.
4. Position sizing is risk-based: each trade risks a fixed fraction of equity
   from entry to stop-loss. Leverage is capped.
5. Only one position at a time (no pyramiding) — representative of a tiny
   $200 account.
6. Parameters are NEVER tuned on the test set. All strategies use common
   textbook defaults declared inside each strategy file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd


# ---------- Config ----------------------------------------------------------

FEE_PER_SIDE = 0.0004      # 0.04% (Binance USDT-M taker)
SLIPPAGE_PER_SIDE = 0.0002 # 0.02% assumed slippage
RISK_PER_TRADE = 0.01      # 1% of equity risked per trade
MAX_LEVERAGE = 3.0         # realistic cap for a $200 retail futures account
START_EQUITY = 200.0


@dataclass
class Trade:
    side: int               # +1 long, -1 short
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp = None  # type: ignore
    exit_price: float = 0.0
    size: float = 0.0        # units of BTC
    stop: float = 0.0
    take: float = 0.0
    pnl: float = 0.0
    fee: float = 0.0
    reason: str = ""


@dataclass
class Result:
    name: str
    equity_curve: pd.Series
    trades: list[Trade]
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    num_trades: int
    win_rate: float
    profit_factor: float
    sharpe: float
    sortino: float
    avg_trade_pct: float
    exposure_pct: float
    period_start: pd.Timestamp
    period_end: pd.Timestamp

    def summary_row(self) -> dict:
        return {
            "strategy": self.name,
            "start": str(self.period_start.date()),
            "end": str(self.period_end.date()),
            "final_equity": round(self.final_equity, 2),
            "return_pct": round(self.total_return_pct, 1),
            "max_dd_pct": round(self.max_drawdown_pct, 1),
            "trades": self.num_trades,
            "win_rate_pct": round(self.win_rate, 1),
            "profit_factor": round(self.profit_factor, 2),
            "sharpe": round(self.sharpe, 2),
            "sortino": round(self.sortino, 2),
            "avg_trade_pct": round(self.avg_trade_pct, 3),
            "exposure_pct": round(self.exposure_pct, 1),
        }


# ---------- Metrics ---------------------------------------------------------


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min() * 100.0)


def _sharpe(returns: pd.Series, periods_per_year: float) -> float:
    r = returns.dropna()
    if len(r) < 2 or r.std(ddof=0) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=0) * np.sqrt(periods_per_year))


def _sortino(returns: pd.Series, periods_per_year: float) -> float:
    r = returns.dropna()
    if len(r) < 2:
        return 0.0
    downside = r[r < 0]
    ds = downside.std(ddof=0) if len(downside) > 0 else 0.0
    if ds == 0:
        return 0.0
    return float(r.mean() / ds * np.sqrt(periods_per_year))


# ---------- Engine ----------------------------------------------------------


SignalFunc = Callable[[pd.DataFrame], pd.DataFrame]


def run_backtest(
    df: pd.DataFrame,
    signal_fn: SignalFunc,
    name: str,
    start_equity: float = START_EQUITY,
    fee: float = FEE_PER_SIDE,
    slippage: float = SLIPPAGE_PER_SIDE,
    risk_per_trade: float = RISK_PER_TRADE,
    max_leverage: float = MAX_LEVERAGE,
) -> Result:
    """Run a single strategy.

    ``signal_fn`` must return a DataFrame aligned with ``df`` containing:
        entry_long   : bool  -> open long on the NEXT bar's open
        entry_short  : bool  -> open short on the NEXT bar's open
        exit_long    : bool  -> close any long on the NEXT bar's open
        exit_short   : bool  -> close any short on the NEXT bar's open
        stop_dist    : float -> absolute price distance to stop-loss
        tp_dist      : float -> absolute price distance to take-profit (0 = none)
    """
    sig = signal_fn(df).reindex(df.index)
    # Strict sanity check — the strategy is not allowed to peek into the future.
    # Shift signals by 1 bar so the decision made on bar i executes at the
    # OPEN of bar i+1.
    entry_long = sig["entry_long"].fillna(False).shift(1).fillna(False).astype(bool).values
    entry_short = sig["entry_short"].fillna(False).shift(1).fillna(False).astype(bool).values
    exit_long = sig["exit_long"].fillna(False).shift(1).fillna(False).astype(bool).values
    exit_short = sig["exit_short"].fillna(False).shift(1).fillna(False).astype(bool).values
    stop_dist = sig["stop_dist"].shift(1).values
    tp_dist = sig["tp_dist"].shift(1).values

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    index = df.index

    equity = start_equity
    equity_curve = np.empty(len(df))
    trades: list[Trade] = []
    open_trade: Optional[Trade] = None
    bars_in_pos = 0

    for i in range(len(df)):
        o = opens[i]
        h = highs[i]
        l = lows[i]

        # 1) Check stop/tp against the CURRENT bar's high/low if there's an
        #    open position (position was opened at an earlier bar's open).
        if open_trade is not None:
            hit_stop = False
            hit_tp = False
            if open_trade.side == 1:
                if open_trade.stop > 0 and l <= open_trade.stop:
                    hit_stop = True
                if open_trade.take > 0 and h >= open_trade.take:
                    hit_tp = True
            else:
                if open_trade.stop > 0 and h >= open_trade.stop:
                    hit_stop = True
                if open_trade.take > 0 and l <= open_trade.take:
                    hit_tp = True

            if hit_stop or hit_tp:
                # If both are hit on the same bar we conservatively assume
                # the stop fires first (worst case).
                if hit_stop:
                    exit_px = open_trade.stop
                    reason = "stop"
                else:
                    exit_px = open_trade.take
                    reason = "tp"
                # Apply slippage on exit against us
                if open_trade.side == 1:
                    exit_fill = exit_px * (1 - slippage)
                else:
                    exit_fill = exit_px * (1 + slippage)
                gross = (exit_fill - open_trade.entry_price) * open_trade.size * open_trade.side
                exit_fee = exit_fill * open_trade.size * fee
                open_trade.fee += exit_fee
                pnl = gross - exit_fee
                equity += pnl
                open_trade.exit_price = exit_fill
                open_trade.exit_time = index[i]
                open_trade.pnl = pnl
                open_trade.reason = reason
                trades.append(open_trade)
                open_trade = None
                bars_in_pos = 0

        # 2) Signal-based exits execute at THIS bar's open (because we shifted
        #    signals by 1 already), i.e. the bar after the signal fired.
        if open_trade is not None and (
            (open_trade.side == 1 and exit_long[i]) or
            (open_trade.side == -1 and exit_short[i])
        ):
            if open_trade.side == 1:
                exit_fill = o * (1 - slippage)
            else:
                exit_fill = o * (1 + slippage)
            gross = (exit_fill - open_trade.entry_price) * open_trade.size * open_trade.side
            exit_fee = exit_fill * open_trade.size * fee
            open_trade.fee += exit_fee
            pnl = gross - exit_fee
            equity += pnl
            open_trade.exit_price = exit_fill
            open_trade.exit_time = index[i]
            open_trade.pnl = pnl
            open_trade.reason = "signal"
            trades.append(open_trade)
            open_trade = None
            bars_in_pos = 0

        # 3) Entries execute at THIS bar's open (signals were shifted).
        if open_trade is None:
            want_long = entry_long[i]
            want_short = entry_short[i]
            if want_long or want_short:
                side = 1 if want_long else -1
                sd = stop_dist[i]
                td = tp_dist[i]
                if sd is None or np.isnan(sd) or sd <= 0:
                    pass  # no valid stop -> skip
                else:
                    # Apply slippage on entry against us
                    entry_fill = o * (1 + slippage) if side == 1 else o * (1 - slippage)
                    risk_dollars = equity * risk_per_trade
                    size = risk_dollars / sd           # units of BTC
                    notional = size * entry_fill
                    max_notional = equity * max_leverage
                    if notional > max_notional:
                        size = max_notional / entry_fill
                        notional = size * entry_fill
                    entry_fee = notional * fee
                    equity -= entry_fee  # pay fee now
                    stop_px = entry_fill - sd if side == 1 else entry_fill + sd
                    take_px = 0.0
                    if td is not None and not np.isnan(td) and td > 0:
                        take_px = entry_fill + td if side == 1 else entry_fill - td
                    open_trade = Trade(
                        side=side,
                        entry_time=index[i],
                        entry_price=entry_fill,
                        size=size,
                        stop=stop_px,
                        take=take_px,
                        fee=entry_fee,
                    )

        # 4) Mark-to-market equity for the curve
        if open_trade is not None:
            mtm_px = closes[i]
            unreal = (mtm_px - open_trade.entry_price) * open_trade.size * open_trade.side
            equity_curve[i] = equity + unreal
            bars_in_pos += 1
        else:
            equity_curve[i] = equity

    # Liquidate open position at the final close (fair / neutral)
    if open_trade is not None:
        last_px = closes[-1]
        exit_fill = last_px * (1 - slippage) if open_trade.side == 1 else last_px * (1 + slippage)
        gross = (exit_fill - open_trade.entry_price) * open_trade.size * open_trade.side
        exit_fee = exit_fill * open_trade.size * fee
        open_trade.fee += exit_fee
        pnl = gross - exit_fee
        equity += pnl
        open_trade.exit_price = exit_fill
        open_trade.exit_time = index[-1]
        open_trade.pnl = pnl
        open_trade.reason = "eod"
        trades.append(open_trade)
        equity_curve[-1] = equity

    equity_series = pd.Series(equity_curve, index=df.index, name="equity")

    # ---- Metrics
    ret_pct = (equity / start_equity - 1) * 100.0
    max_dd = _max_drawdown(equity_series)
    n_trades = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = 100.0 * len(wins) / n_trades if n_trades else 0.0
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    avg_trade_pct = float(np.mean([t.pnl / start_equity * 100 for t in trades])) if trades else 0.0

    bar_returns = equity_series.pct_change().fillna(0.0)
    bars_per_year = 365 * 24 * 4  # 15-minute bars
    sh = _sharpe(bar_returns, bars_per_year)
    so = _sortino(bar_returns, bars_per_year)

    total_bars = len(df)
    in_pos_bars = sum(
        int(((df.index >= t.entry_time) & (df.index <= t.exit_time)).sum()) for t in trades
    )
    exposure = 100.0 * in_pos_bars / total_bars if total_bars else 0.0

    return Result(
        name=name,
        equity_curve=equity_series,
        trades=trades,
        final_equity=equity,
        total_return_pct=ret_pct,
        max_drawdown_pct=max_dd,
        num_trades=n_trades,
        win_rate=win_rate,
        profit_factor=pf if pf != float("inf") else 999.0,
        sharpe=sh,
        sortino=so,
        avg_trade_pct=avg_trade_pct,
        exposure_pct=exposure,
        period_start=df.index[0],
        period_end=df.index[-1],
    )
