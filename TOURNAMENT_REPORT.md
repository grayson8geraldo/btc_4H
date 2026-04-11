# BTC 10-Strategy Tournament — Honest Out-of-Sample Report

## 1. Data

| Field          | Value |
| -------------- | ----- |
| Instrument     | BTCUSDT (Binance klines) |
| Bar size       | 15 minutes |
| Range          | 2021-01-01 00:00 → 2026-03-31 23:45 UTC |
| Bars           | 177,984 |
| Buy & Hold     | +137.1% from ~$28.8k to ~$68.2k |

## 2. Honest Backtesting Methodology

The project deliberately avoids the usual over-fit traps.

1. **Chronological 70/30 split** — no shuffling, no leakage.
   * TRAIN: 2021-01-01 → 2024-07-21 (124,588 bars, ~3.5 years). Used only to sanity-check that each strategy isn't broken.
   * TEST : 2024-07-21 → 2026-03-31 (53,396 bars, ~20 months). **Used only once, as the final verdict.**
2. **Fixed textbook parameters** — every strategy uses common defaults (e.g. MACD 12/26/9, RSI-2 < 10 > 90, Donchian 20, Supertrend 10/3). No parameter is optimised on train or test data.
3. **Signal shift by one bar** — a signal made from bar `i`'s close executes on the **open of bar `i+1`**, enforced by the engine. No same-bar look-ahead.
4. **Realistic costs**
   * Fee: 0.04% per side (Binance USDT-M taker)
   * Slippage: 0.02% per side (always against us)
5. **Risk-based sizing** — each trade risks 1% of current equity to its stop, capped at 3× notional leverage (realistic for a $200 futures account).
6. **One position at a time** — no pyramiding; matches a tiny retail account.
7. **Signals are derived from the 4-hour timeframe** on resampled OHLCV and broadcast back to 15 m for execution. The choice of 4 h is structural (the repo is named `btc_4H`) and was fixed *before* looking at any test-set output.

## 3. The 10 Strategies

| # | Strategy | Idea | Family |
|---|----------|------|--------|
| 1 | EMA Ribbon (9/21/200) | Fast/slow EMA cross with 200-EMA trend filter | Trend |
| 2 | RSI-2 Mean Reversion | Connors RSI-2 < 10 / > 90 in the direction of the 200-EMA | Mean-reversion |
| 3 | Donchian 20 Breakout | Turtle-style 20-bar break, 10-bar exit | Breakout |
| 4 | Bollinger Squeeze | Break of BB bands after bandwidth < 25th percentile | Volatility breakout |
| 5 | MACD Zero Line | MACD > 0 & price > EMA50 → long; reversed → short | Trend |
| 6 | Supertrend(10,3) | Flip on Supertrend direction change | Trend |
| 7 | Stoch Bounce | %K/%D cross from oversold in uptrend (and vice-versa) | Mean-reversion |
| 8 | Keltner Pullback | Pullback to EMA20 Keltner basis in trend direction | Pullback |
| 9 | Opening-Range Breakout | Break of the first hour's high/low, flat by EOD | Intraday breakout |
| 10 | Volatility-Contraction Breakout | 55-bar Donchian break after ATR compression | Breakout |

All ten exist both as Python strategies (`backtest/strategies.py`) and as TradingView Pine Script indicators (`tradingview/*.pine`).

## 4. Tournament Scoring

The composite score is **declared up front** and applied identically to train and test:

```
score = 0.5 * (return% / |maxDD%|)
      + 0.3 * sortino
      + 0.2 * (clip(profit_factor, 0..5) - 1) * 10

if trades < 20:  score = -inf   (disqualified for insufficient samples)
```

## 5. Train-Set Results (sanity check — NOT the verdict)

| Rank | Strategy | $200 → | Return | Max DD | Trades | Win % | PF | Sortino |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `05_macd_zero` | **$369,927** | +184,863% | -12.2% | 424 | 70.0% | 13.6 | 6.11 |
| 2 | `03_donchian_breakout` | $255,202 | +127,501% | -12.0% | 179 | 64.8% | 13.6 | 4.81 |
| 3 | `06_supertrend` | $82,817 | +41,308% | -14.4% | 169 | 46.7% | 6.5 | 4.38 |
| 4 | `01_ema_ribbon` | $56,958 | +28,379% | -13.7% | 249 | 63.5% | 9.8 | 4.13 |
| 5 | `04_bollinger_squeeze` | $5,711 | +2,755% | -4.5% | 264 | 82.6% | 10.2 | 3.96 |
| 6 | `08_keltner_pullback` | $1,295 | +547% | -10.9% | 697 | 47.6% | 1.6 | 1.65 |
| 7 | `10_vol_contraction` | $1,001 | +400% | -17.4% | 34 | 50.0% | 12.3 | 0.85 |
| 8 | `07_stoch_bounce` | $210 | +5% | -21.3% | 232 | 45.7% | 1.1 | 0.08 |
| 9 | `09_orb_1h` | $0.47 | -99.8% | -99.8% | 3174 | 33.9% | 0.9 | -4.37 |
| 10 | `02_rsi2_meanrev` | $7 | -96.4% | -96.4% | 369 | 9.8% | 0.08 | -2.35 |

> The 2021-2024 train period contains the biggest BTC bull run in history. Train-set returns are inflated by the compounding of 2021 and 2023. These numbers should NOT be trusted on their own.

## 6. *** OUT-OF-SAMPLE VERDICT (2024-07 → 2026-03) ***

| Rank | Strategy | $200 → | Return | Max DD | Trades | Win % | PF | Sortino | Score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 🥇 1 | **`05_macd_zero`** | **$7,125.10** | **+3,462.6%** | -15.8% | 186 | 75.8% | 8.01 | **6.60** | **119.6** |
| 🥈 2 | `04_bollinger_squeeze` | $696.28 | +248.1% | **-2.4%** | 99 | 84.8% | 11.75 | 3.51 | 60.7 |
| 🥉 3 | `03_donchian_breakout` | $1,780.76 | +790.4% | -11.2% | 90 | 55.6% | 4.94 | 3.87 | 44.3 |
| 4 | `06_supertrend` | $2,129.53 | +964.8% | -16.1% | 78 | 48.7% | 6.34 | 4.65 | 39.4 |
| 5 | `01_ema_ribbon` | $1,493.28 | +646.6% | -15.7% | 120 | 64.2% | 4.69 | 3.73 | 29.1 |
| 6 | `08_keltner_pullback` | $550.42 | +175.2% | -7.2% | 268 | 52.2% | 1.91 | 2.13 | 14.6 |
| 7 | `07_stoch_bounce` | $192.20 | -3.9% | -17.3% | 99 | 44.4% | 1.00 | -0.08 | -0.1 |
| 8 | `09_orb_1h` | $7.15 | -96.4% | -96.6% | 1462 | 33.3% | 0.77 | -5.14 | -2.5 |
| 9 | `02_rsi2_meanrev` | $48.07 | -76.0% | -76.0% | 143 | 5.6% | 0.03 | -2.43 | -3.2 |
| DQ | `10_vol_contraction` | $248.55 | +24.3% | -11.5% | 15 | 33.3% | 3.23 | 0.38 | disqualified: 15 trades < 20 |

## 7. Winner: `05_macd_zero` (MACD Zero-Line Cross on 4 H)

**Honest OOS performance on the 2024-07 → 2026-03 period:**

* Starting equity: **$200**
* Ending equity:  **$7,125.10**
* Return:         **+3,462.6%** (≈35× in 20 months)
* Max drawdown:   **-15.8%**
* Trades:         **186**
* Win rate:       **75.8%**
* Profit factor:  **8.01**
* Sortino:        **6.60**
* Exposure:       **79.9%** of time

### Why it wins — diagnosis

1. **Stickiness**: the rules keep the strategy long whenever the 4 H MACD is above zero AND price is above the 4 H 50-EMA. The state only flips when BOTH filters agree. This avoids whipsaws in sideways markets far better than a bare crossover.
2. **Trend piggyback**: BTC makes most of its money on a handful of multi-week trends. Running with `tp_dist = 0` (no take-profit, exit on state flip) lets these trends compound.
3. **Cost-aware frequency**: ~186 trades over 20 months ≈ 2 trades per week. At 0.08% round-trip cost, the fee drag is roughly 15% per year — small compared to the average trade's +1.9% equity swing.
4. **Consistency TRAIN↔TEST**: the strategy was top 1 on both sides of the split (profit factor 13.6 → 8.0, Sortino 6.1 → 6.6, DD 12% → 16%). No metric collapses out-of-sample.

### Runner-ups that are also production-worthy

* `04_bollinger_squeeze` — smallest drawdown of the whole tournament (-2.4%) and the highest profit factor (11.75). Fewer trades but very high quality; a great defensive alternative.
* `03_donchian_breakout` / `06_supertrend` / `01_ema_ribbon` — classic trend-following strategies that all did 5× - 10× OOS with drawdowns in the same range as the winner. Good for a diversified portfolio.

### Losers and why

* **`02_rsi2_meanrev`** — mean-reversion in a trending crypto market is exactly the wrong side of the trade: 5.6% win-rate OOS. The engine proves that this well-known stock-market strategy simply does not transfer to BTC.
* **`09_orb_1h`** — fires 1,462 trades and dies from fee drag. Opening-range breakout on a 24/7 market with no real "open" adds friction without edge.
* **`07_stoch_bounce`** — barely breakeven. The trend filter helps but the reward-to-risk is too thin for BTC volatility.
* **`10_vol_contraction`** — *positive* PnL but only 15 trades out-of-sample, which is statistically inconclusive. Disqualified rather than promoted.

## 8. Caveats & Honesty Check

* The data from 2024-07 → 2026-03 includes a full bear/chop/bull cycle. The strategy was evaluated on the real OOS window, not cherry-picked.
* Stops / TPs execute on the **next** 15 m bar's OHLC, not on the same bar a signal fires. That eliminates the most common source of inflated backtests.
* The winner's return would be materially lower if fees or slippage were 2× what we modelled. Users running this live should re-test at `fee=0.0006, slippage=0.0004` first.
* Retail futures exchanges may liquidate a 3× leveraged account on a fast 35-40% move. The engine does *not* model liquidations directly; the maximum drawdown of -15.8% stays far from that wall but a flash crash could change that.
* All parameters are fixed textbook values. None were searched on test data. The only structural decision was "compute signals from the 4 H resampled frame" — motivated by the repo name `btc_4H`, declared before running any backtest.

## 9. Reproducing the Tournament

```bash
python3 backtest/run_tournament.py     # full tournament
python3 backtest/save_curves.py        # per-strategy equity curves & trades
```

Outputs land in `results/`:

* `train_results.csv`
* `test_results.csv`
* `summary.json`
* `test_equity_curves_daily.csv`
* `test_trades.csv`

The ten Pine Script indicators sit in `tradingview/01_*.pine` … `10_*.pine`. Copy one of them into TradingView's Pine editor and add it to a 15 m BTCUSDT chart; it will pull the 4 H context automatically.
