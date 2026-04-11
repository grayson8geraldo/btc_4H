# Live BTC Signal Bot — Strategy #5 (MACD Zero 4H)

A self-contained Python 3 bot that reproduces the tournament winner strategy
live, pulls 4-hour BTC candles from the public Binance API, and pushes
trade alerts to your Telegram. **Zero pip dependencies** — uses only the
Python standard library.

## What you get in each alert

```
BTC Tournament #5 — MACD Zero 4H
🟢 LONG  BTCUSDT

Entry       ~ $70,412.30
Stop-loss   $ 69,784.10  (−$628.20)
Take-profit none — exit on opposite / flat signal

Risk model (equity $200.00):
  risk    1.0% = $2.00
  size    0.00318 BTC
  notion. $224.11
  lev     1.12x  (cap 3x)

4H context:
  MACD    +12.47
  EMA50   $69,050.20
```

Signals are strictly 1:1 with the backtest — see `bot/test_bot_logic.py`
for proof (max |bot − backtest| across 5 years of 4H data = 0.000000).

## Option A — GitHub Actions (fully free, no server, RECOMMENDED)

Run the bot for free directly from this GitHub repository using GitHub
Actions. A workflow at `.github/workflows/live-signal-bot.yml` runs the
bot every 15 minutes on GitHub's runners. No VPS, no TradingView
subscription, no credit card.

**Budget check:** each run takes ~30 seconds. `*/15 * * * *` consumes
roughly 1,440 minutes per month — fits the 2,000-minute free quota on
private repos. On **public** repos GitHub Actions minutes are
**unlimited and free**, so you don't need to worry at all.

### 1. Create the Telegram bot

1. Open Telegram, message **@BotFather**, send `/newbot`, pick a name.
2. Save the bot token BotFather returns (e.g. `123456789:ABC-DEF...`).
3. Start a chat with your new bot.
4. Send `/start` to your bot.
5. Get your numeric chat id from **@userinfobot**.

### 2. Add secrets to the repo

On GitHub, go to **Settings → Secrets and variables → Actions → New repository secret**
and add the following secrets:

| Name                  | Value                              |
| --------------------- | ---------------------------------- |
| `TELEGRAM_BOT_TOKEN`  | the BotFather token                |
| `TELEGRAM_CHAT_ID`    | your numeric Telegram chat id      |

Optional **variables** (Settings → Secrets and variables → Actions → Variables tab):

| Name           | Default | Meaning                              |
| -------------- | ------- | ------------------------------------ |
| `BOT_EQUITY`   | `200`   | Account size in USD                  |
| `BOT_RISK_PCT` | `0.01`  | Fraction of equity risked per trade  |
| `BOT_MAX_LEV`  | `3.0`   | Maximum leverage                     |

### 3. Enable the workflow

1. On GitHub, open the **Actions** tab.
2. If Actions is disabled, click **I understand, enable**.
3. In the left panel pick **BTC Live Signal Bot**.
4. Click **Run workflow** → pick mode `test` to send a test Telegram message.
5. If the test arrives, pick `status` to see the current state, or just
   wait — the schedule (`*/15 * * * *`) will take over automatically.

### 4. That's it

The workflow will:
- fetch fresh BTCUSDT 4-hour candles from Binance,
- compute MACD(12,26,9) + EMA(50) on 4H and ATR(14) on 15m,
- detect state transitions against the persisted `bot/ci_state.json`,
- send a detailed Telegram message on every transition,
- commit back the updated state file so subsequent runs see it.

State commits use `[skip ci]` so they don't trigger any extra workflow.
If they pollute your history you can always squash them later.

### 5. Scaling and safety

- If you need fewer runs (e.g. to stay on the 2000-min free tier),
  change the cron line in the workflow file to `*/30 * * * *`. The
  strategy operates on 4-hour bars so a 30-minute polling interval
  detects every signal with at most 30 minutes of delay.
- If you want to pause the bot, disable the workflow on the Actions tab.
- If you want to change the equity or risk, update the variables; the
  next run will pick them up automatically.

## Option B — TradingView alert (NOT free in practice)

TradingView's free tier only supports alerts on the **current chart
only** and does not deliver Telegram notifications. To get useful
alerts from Pine Script `alert()` calls across sessions, TradingView
typically requires a paid plan (Essential or above). If you already pay
for it:

1. Open TradingView, paste `tradingview/05_macd_zero.pine` into the Pine
   editor, save.
2. Add it to a **15-minute BTCUSDT** chart.
3. Configure the inputs: **Account equity**, **Risk %**, **Max leverage**.
4. Right-click the chart → *Add alert*.
5. *Condition* = `BTC Tournament #5 — MACD Zero 4H (WINNER)`, option
   **"Any alert() function call"**.
6. *Trigger*    = `Once Per Bar Close`.
7. *Delivery*   = notifications to your mobile / email / webhook.

Otherwise, use Option A above — it does the same thing for free.

## Option C — Run the Python bot on your own machine

Use this if you prefer running on your own hardware — a laptop that's
always on, a Raspberry Pi, a home server, an Android phone with Termux,
or a VPS. Anything with Python 3.8+ and outbound HTTPS works.

### 1. Environment variables

```bash
export TELEGRAM_BOT_TOKEN="123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
export TELEGRAM_CHAT_ID="987654321"
export BOT_EQUITY="200"     # optional, default 200
export BOT_RISK_PCT="0.01"  # optional, default 1%
export BOT_MAX_LEV="3.0"    # optional, default 3x
```

### 2. Test the pipeline

```bash
python3 bot/live_macd_bot.py --test      # sends "Bot is alive" to Telegram
python3 bot/live_macd_bot.py --status    # prints current state, no alert
```

### 3. Run modes

**One-shot (for cron):**

```bash
python3 bot/live_macd_bot.py --once
```

**Long-running loop (check every 15 min):**

```bash
python3 bot/live_macd_bot.py --loop 900
```

**Custom equity:**

```bash
python3 bot/live_macd_bot.py --once --equity 500
```

### 4. Install as a cron job (Linux / macOS)

Open crontab: `crontab -e`, add:

```cron
*/15 * * * *  sleep 30 && cd /path/to/btc_4H && \
  TELEGRAM_BOT_TOKEN="..." TELEGRAM_CHAT_ID="..." \
  /usr/bin/python3 bot/live_macd_bot.py --once >> bot/bot.log 2>&1
```

The `sleep 30` offset gives Binance 30 seconds to finalise the most recent
bar before the bot fetches it, eliminating the race where a 4H bar has just
closed but the API still returns the open value.

### 5. Install as a systemd service (Linux)

Create `/etc/systemd/system/btc-macd-bot.service`:

```ini
[Unit]
Description=BTC Tournament #5 MACD Zero bot
After=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/btc_4H
Environment=TELEGRAM_BOT_TOKEN=123456789:ABC...
Environment=TELEGRAM_CHAT_ID=987654321
Environment=BOT_EQUITY=200
ExecStart=/usr/bin/python3 /home/pi/btc_4H/bot/live_macd_bot.py --loop 900
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now btc-macd-bot.service
journalctl -u btc-macd-bot.service -f
```

## How the bot stays honest

1. **It only reads CLOSED 4H bars.** The partial in-progress bar is
   discarded before the MACD / EMA50 are computed, so the state never
   flickers mid-candle.
2. **State is persistent across runs.** `bot/bot_state.json` stores the
   last detected state and the last bar timestamp. No duplicate alerts
   even if you run the bot every minute.
3. **First-run silence.** The very first time the bot runs it establishes
   the current state without sending an alert (you don't want a sudden
   "LONG at $X" where X is a random mid-trend price).
4. **Binance public API, no keys.** The bot only reads public kline data —
   it does not and cannot place orders. Execution stays 100% manual, by
   design.

## Verifying the bot matches the backtest

```bash
python3 bot/test_bot_logic.py
```

This loads the same 5 years of 15m data from the repo, runs the bot's
pure-Python MACD / EMA50 / ATR functions against the backtest's pandas
implementations, and asserts they agree to the bit.

Expected output:

```
Max |MACD(bot) - MACD(backtest)|  = 0.000000
Max |EMA50(bot) - EMA50(backtest)| = 0.000000
State mismatches on 11124 4H bars: 0
Max |ATR14(bot) - ATR14(backtest)|  = 0.000000
OK — bot indicators match the backtest exactly.
```

## FAQ

**Q: Does the bot place orders on the exchange?**  No. It is strictly a
notifier. Execution is manual — the backtest assumed market-order entries
with realistic slippage, so you should place a market order yourself when
the alert fires and set the stop-loss to the price shown in the message.

**Q: Why 4H and not faster?**  Because faster signals get eaten alive by
fees. The tournament proved that 15-minute native signals lose money on
BTC after 0.08% round-trip costs. 4H signals trade only ~186 times over 20
months, which keeps fee drag negligible while catching every multi-week
trend.

**Q: Can I use this for USDT-M futures / Bybit / OKX?**  Yes — the bot
reads BTCUSDT from spot Binance but the 4H structure is identical across
venues; differences between spot and perpetual are tiny at the 4H
granularity. Just make sure the venue you trade on has a BTCUSDT market
with perp contracts so you can short.

**Q: What if the bot misses a signal because my laptop was off?**  When it
comes back online the next `--once` run will detect that the stored state
no longer matches the current computed state and send a catch-up alert.
The alert text is the same — you just enter at whatever price BTC is at
that moment and set the stop-loss.

**Q: Which level of leverage?**  The backtest cap is 3x. The bot sizes
positions so that a stop-out equals 1% of equity and the resulting
notional is capped at 3× equity. Going higher is unsafe: fast flash
crashes can liquidate you before the stop triggers.
