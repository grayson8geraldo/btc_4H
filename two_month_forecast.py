"""Ответ на вопрос: сколько можно заработать за 2 месяца с $200.

Берём все сделки из v2 (stack_J) бэктеста, прогоняем сделки с реальной
капитализацией $200 и считаем скользящее 2-месячное окно.
"""

import numpy as np
import pandas as pd

from backtest import load_15m, run_backtest


CAPITAL = 200.0

# Реалистичные оценки комиссий/проскальзывания для фьючерса на Binance:
#   0.04 % taker-комиссия  *  4 стороны (вход + 2 выхода половинами)
#   ~0.05 % суммарный slippage
# Итого ~0.21 % от номинала позиции за сделку.  При стопе ~3% и риске 1%
# позиция = капитал * 1% / 3% ~ капитал/3, значит издержки ~ 0.07 % капитала.
COST_PER_TRADE_PCT = 0.0007   # 0.07 % от капитала на одну полную сделку


def simulate(trades, capital: float, risk_frac: float) -> pd.DataFrame:
    """Возвращает equity-кривую, считая P&L в долларах и учитывая комиссии."""
    rows = []
    eq = capital
    for t in trades:
        r = t.r_mult
        pnl = eq * risk_frac * r
        pnl -= eq * COST_PER_TRADE_PCT
        eq += pnl
        rows.append({"ts": t.exit_time, "r": r, "pnl_usd": pnl, "equity": eq})
    return pd.DataFrame(rows)


def window_stats(df: pd.DataFrame, days: int = 60) -> pd.DataFrame:
    """Для каждого дня считает, сколько заработано за последующие N дней."""
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"])
    rows = []
    start_ts = df["ts"].iloc[0]
    end_ts = df["ts"].iloc[-1] - pd.Timedelta(days=days)
    t = start_ts
    while t <= end_ts:
        hi = t + pd.Timedelta(days=days)
        sub = df[(df["ts"] >= t) & (df["ts"] < hi)]
        if not sub.empty:
            start_eq = df[df["ts"] < t]["equity"].iloc[-1] if (df["ts"] < t).any() else CAPITAL
            end_eq = sub["equity"].iloc[-1]
            rows.append({
                "window_start": t.date(),
                "trades": len(sub),
                "pnl_usd": end_eq - start_eq,
                "return_pct": (end_eq / start_eq - 1) * 100,
            })
        t += pd.Timedelta(days=7)
    return pd.DataFrame(rows)


def main():
    print("Загружаю данные ...")
    df15 = load_15m()
    r = run_backtest(df15)
    trades = r.trades
    print(f"Всего сделок за {(trades[-1].exit_time - trades[0].entry_time).days} дней: {len(trades)}")
    avg_per_month = len(trades) / ((trades[-1].exit_time - trades[0].entry_time).days / 30.44)
    print(f"Средняя частота: {avg_per_month:.2f} сделки в месяц")
    print(f"Средний R на сделку: {np.mean([t.r_mult for t in trades]):.3f}R")
    print()

    scenarios = [
        ("консервативно 1% риска", 0.01),
        ("стандарт    2% риска", 0.02),
        ("агрессивно  3% риска", 0.03),
        ("очень агр.  5% риска", 0.05),
    ]

    for name, risk in scenarios:
        eq_df = simulate(trades, CAPITAL, risk)
        wins = window_stats(eq_df, days=60)
        if wins.empty:
            continue
        best = wins.loc[wins["pnl_usd"].idxmax()]
        worst = wins.loc[wins["pnl_usd"].idxmin()]
        mean = wins["pnl_usd"].mean()
        median = wins["pnl_usd"].median()
        p25 = wins["pnl_usd"].quantile(0.25)
        p75 = wins["pnl_usd"].quantile(0.75)
        pos_rate = (wins["pnl_usd"] > 0).mean() * 100
        mean_trades = wins["trades"].mean()

        print(f"--- {name} (старт ${CAPITAL:.0f}) ---")
        print(f"   ср. сделок за 2 мес : {mean_trades:.1f}")
        print(f"   медиана P&L         : ${median:+.2f}  ({median/CAPITAL*100:+.1f} %)")
        print(f"   средний P&L         : ${mean:+.2f}  ({mean/CAPITAL*100:+.1f} %)")
        print(f"   25-й перцентиль     : ${p25:+.2f}")
        print(f"   75-й перцентиль     : ${p75:+.2f}")
        print(f"   лучшие 2 месяца    : ${best['pnl_usd']:+.2f}  ({best['window_start']}, {int(best['trades'])} сделок)")
        print(f"   худшие 2 месяца    : ${worst['pnl_usd']:+.2f}  ({worst['window_start']}, {int(worst['trades'])} сделок)")
        print(f"   % положительных окон: {pos_rate:.0f} %")
        print()


if __name__ == "__main__":
    main()
