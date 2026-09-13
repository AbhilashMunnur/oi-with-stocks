# RSI + candle stock scanner

Scans NSE F&O stocks on **live Angel One data** and takes a 3rd-month stock-futures paper trade when RSI tags **70 / 30** and a reversal candle prints:

- **Short** — inverted hammer / weak middle body / strong red
- **Long** — hammer / weak middle body / strong green

Either next-day (yesterday tagged, today reverses) or same-day (from **15:15 IST**, today's bar already reversed) qualifies. Cash is never the fill.

Two paper books share those entries:

| Book | Entry RSI | Stop | Ledger |
|------|-----------|------|--------|
| **RSI_CandlePattern** (live) | 70 / 30 | Farther of the reversal-bar high/low and 2% | `data/rsi_candle_3m_2w_paper_book.json` |
| **RSI_Candle_3Lot** (final) | 70 / 30 | Flat 1.5% | `data/rsi_candle_3lot_paper_book.json` |

The scanner screens every name at the global `rsi.call_threshold` / `put_threshold` (70/30). A book may optionally set its own `rsi_call_threshold` / `rsi_put_threshold` and then only takes the alerts whose entry RSI clears its level; neither book does today.

A third book, **Heikin_Ashi** (`data/heikin_ashi_paper_book.json`, flat 1.5% stop), uses the same ladder but its own entries. RSI on the normal OHLC chart must have tagged 70/30 within the last 10 sessions; then on the Heikin-Ashi chart a strong trend-colour candle (body ≥ 50% of range), zero or more weak candles (body ≤ 40%, sticks either side), then today's HA candle turns the opposite colour — any body size. The normal candle shape is not required. Names whose HA chart is still in the weak-candle base go to the Telegram digest as "base forming — waiting for a red/green HA candle" and are not traded. Entries from 15:15 IST with the live price folded into today's bar. Settings under `heikin_ashi:` (`min_weak_candles`, `opposite_needs_body`, `require_normal_candle` restore the stricter variants) and `heikin_ashi_paper_trading:`.

All books: ₹4 Cr, 3 lots. Lot 1 books at 5% or SMMA 21; lot 2 at 12% or SMMA 50; the runner exits on RSI 30/70 or a strong close back through SMMA 21. Telegram sends the reversal lists (RSI_CandlePattern and Heikin_Ashi separately) plus a PNG dashboard per book.

Live prices, RSI and candles come from Angel One SmartAPI.

## Setup

```bash
cd "OI with stocks"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Angel One credentials

1. Create an app at [smartapi.angelone.in](https://smartapi.angelone.in), choosing the
   **Trading API** type — the narrower Market Feed and Historical Data keys only work
   for their one function, and the scanner needs both quotes and candles
2. Enable TOTP at [smartapi.angelone.in/enable-totp](https://smartapi.angelone.in/enable-totp)
   and save the secret shown with the QR code
3. Run the guided setup, which prompts for each value with hidden input and writes
   `.env` for you:
   ```bash
   python scripts/setup_angelone.py
   ```
   Or copy `.env.example` to `.env` and fill it in by hand:
   ```
   ANGEL_API_KEY=your_api_key
   ANGEL_CLIENT_CODE=your_client_code
   ANGEL_PIN=your_pin
   ANGEL_TOTP_SECRET=your_totp_secret
   ```

`ANGEL_TOTP_SECRET` is the text string shown next to the 2FA QR code, not the
6-digit code that rotates every 30 seconds. Login then happens automatically on
every run, so there is no daily step.

Confirm everything works before relying on alerts:

```bash
python scripts/check_angelone.py            # defaults to RELIANCE
python scripts/check_angelone.py HDFCBANK
```

It verifies login, live LTP, RSI and the option chain, then prints what it found.

From 1 Apr 2026 Angel One requires a registered static IP, but only for **order
execution**. This scanner is read-only and places no orders, so it is unaffected.

## Configuration

Edit `config.yaml`:

| Setting | Default | Description |
|---------|---------|-------------|
| `rsi.call_threshold` | 70 | Stretch bar that can set up a short |
| `rsi.put_threshold` | 30 | Stretch bar that can set up a long |
| `data.history_days` | 400 | Daily candles for RSI and SMMA |
| `watchlist` | `all` | `all` for every F&O stock, or an explicit list of symbols |
| `schedule.interval_minutes` | 30 | How often to scan during market hours |
| `notifications.cooldown_minutes` | 30 | Minimum gap before repeating the same alert |

The F&O universe (208 stocks as of writing) and every lot size are read from Angel
One's instrument master rather than hardcoded, so contract changes and new listings
are picked up automatically when the daily cache refreshes.

## Usage

**Single scan of every F&O stock:**
```bash
python main.py --once
```

**Scan one stock:**
```bash
python main.py --once --symbol RELIANCE
```

**Scheduled mode (scans every 30 min, 09:30–15:45 on weekdays):**
```bash
python main.py
```

Scans only run inside that window. Outside it — after hours, weekends — the process
stays up but idles, printing a skip line instead of calling the API, so it costs
nothing while waiting for the next session. It also keeps running if a single scan
fails, so a transient API error does not end the day. Angel One tokens expire daily,
so it re-logins automatically when the session is rejected or the date rolls over.
Press Ctrl+C to stop.

To keep it alive after closing the terminal:

```bash
nohup python -u main.py > scanner.log 2>&1 &
tail -f scanner.log
```

## US Nasdaq RSI alerts (daily + weekly)

Separate once-a-day scan of **Nasdaq-100** names. After US cash close it
sends two Telegram messages (no OI filter, no paper trades):

- **Daily RSI ≤ 32**
- **Weekly RSI ≤ 35**

```bash
python us_rsi_main.py
```

Hosted via `.github/workflows/us_rsi_scan.yml` at **21:30 UTC** weekdays
(≈ **3:00am IST** next day). Config: `config_us_rsi.yaml`, universe:
`data/nasdaq100.txt`.

## India Nifty RSI alerts (daily)

Same style for **Nifty-200**: once after NSE cash close, **daily RSI ≤ 32** and
**weekly RSI ≤ 32** (two Telegram messages).

```bash
python in_rsi_main.py
```

Hosted via `.github/workflows/in_rsi_scan.yml` at **10:30 UTC** weekdays
(**4:00pm IST**). Config: `config_in_rsi.yaml`, universe: `data/nifty200.txt`.

## Telegram notifications

```bash
python scripts/setup_telegram.py
```

It asks for the bot token from [@BotFather](https://t.me/BotFather), finds your chat
ID automatically, sends a test message and writes both values to `.env`.

Each 15:15 scan sends an **RSI_CandlePattern** list of names that qualified,
then a **PNG dashboard** for each paper book (open lots, P&L, today's events).
Half-hour mark-only slots skip the 210-name screen and only refresh those
dashboards.

```
RSI_CandlePattern alerts — 12 Sep 2026 15:15

SHORT (after RSI ≥ 70 strong bull)
• TITAN: RSI 74.3 | ₹5,090.00 | inverted hammer
```

Telegram is enabled in `config.yaml` but only used when both values are present,
so it stays quiet until you set it up.

### Sending to more than one person

```bash
python scripts/add_telegram_recipient.py
```

Telegram only lets a bot message people who have contacted it first, so the new
person must open the bot and press **Start** before running this. The script waits
for them, then appends their chat ID to `TELEGRAM_CHAT_ID`, which accepts a comma
separated list. A failure to reach one recipient does not stop the others.

Run `./scripts/sync_github_secrets.sh` afterwards so the hosted scans pick up the
new list.

For a larger group, create a Telegram group, add the bot to it, and use the group's
chat ID instead — then people can be added or removed without touching the config.

## Running without your own machine

Scans run on **GitHub Actions** — your Mac does not need to stay on.

Schedule: **09:30 → 15:30 IST**, one scan per 30‑minute slot on weekdays.
GitHub’s cron can be late or skip a tick, so the workflow **polls every 15
minutes** and a slot guard runs each half-hour slot exactly once (a missed
09:30 tick is picked up at 09:45, and so on).

1. Set up Telegram first — a hosted run has no terminal to print to
2. Push the credentials in `.env` up as repository secrets:
   ```bash
   ./scripts/sync_github_secrets.sh
   ```
   Re-run it whenever you rotate a credential. To do it by hand instead, add
   `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, `ANGEL_TOTP_SECRET`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON`
   under **Settings → Secrets and variables → Actions**
3. Open the **Actions** tab, pick **OI + RSI scan**, and use **Run workflow** to
   trigger a scan by hand and confirm the alert reaches your phone

Exchange holidays are not detected, so a holiday scan may report the previous
session’s values.

## How it works

```
1. At 15:15 IST, fetch 3rd-month futures LTP for every F&O name
2. Read daily OHLC + RSI (cached closes + live futures as today's close)
3. Take a trade if either next-day or same-day reversal qualifies
4. Open / mark both paper books and send Telegram
```

Half-hour slots before 15:15 only mark open paper to futures LTP and send the
dashboard. Daily closes are cached in `.cache/` and seeded from
`data/daily_closes_seed.json` on hosted runners so they do not refetch 208
candle series.

## Paper trading

Every qualifying candle alert is taken as a simulated **3rd-month** stock-futures
trade. Settings live under `rsi_candle_2w_paper_trading` (live book — do not
change stops on names already open) and `rsi_candle_3lot_paper_trading` (final
book: flat 1.5% stop) in `config.yaml`.

| Rule | Action |
|------|--------|
| 5% in our favour, or SMMA 21 | Close lot 1 |
| 12% in our favour, or SMMA 50 | Close lot 2 |
| RSI 30 (shorts) / 70 (longs), or a strong close back through SMMA 21 | Close the runner |
| Live book: farther of candle high/low and 2% | Stop remaining lots (except the runner) |
| Final book: flat 1.5% | Stop remaining lots (except the runner) |
| Last-Tuesday stock monthly expiry | No new entries |
| Laboratory names | Never short; longs still allowed |

### The trade journal

Every closed trade is appended to that book's CSV (`data/rsi_candle_3m_2w_paper_trades.csv`
or `data/rsi_candle_3lot_paper_trades.csv`), one row per lot exited,
so a scale-out produces two rows:

```
Symbol,Buy/Sell,Entry date,Entry price,Entry RSI,Exit date,Exit price,Exit RSI,Holding trading period,Capital needed,Profit/loss,Exit reason
TITAN,Sell,10-Aug-26,5090.0,74.6,24-Aug-26,4784.6,41.2,10,178150*1,53445,first_target
TITAN,Sell,10-Aug-26,5090.0,74.6,28-Aug-26,4326.5,28.4,14,178150*1,133612,second_target
```

That matches your Nifty backtesting sheet columns, with `Symbol` and `Exit reason`
added for the multi-stock paper book. Rows go to a separate **Paper trades** tab
so the historical Nifty results are left alone.

`Holding trading period` counts weekdays, so a Friday-to-Monday trade reads as 1.

### Mirroring to Google Sheets

Set `google_sheet_id` and `google_worksheet` in `config.yaml`, then:

1. In [Google Cloud Console](https://console.cloud.google.com), create a project and
   enable the **Google Sheets API**
2. Create a **service account** and download its JSON key
3. Share the spreadsheet with the service account's email, as **Editor**
4. Store the JSON key as `GOOGLE_SERVICE_ACCOUNT_JSON` — the whole file contents in
   `.env`, and the same as a repository secret for the hosted scans

Without those credentials the CSV is still written; only the mirroring is skipped.
A Sheets failure never blocks the local record.

Each book also appends a row to its **Portfolio Summary** tab after every
completed half-hour scan. Each snapshot records date, time, open-position count,
blocked capital, total P&L, cumulative realised P&L, and current unrealised P&L.

### What the simulation assumes

These matter when comparing against what real fills would have produced:

- **Prices are only seen every 30 minutes.** A move that pierces a level and
  recovers inside that window is never observed, so some real stop-outs are missed.
- **Targets and stops fill at their exact trigger levels**, like resting orders. A
  price gapping straight past a stop would fill worse in reality.
- **When one snapshot shows both a stop and a target reached**, the stop is taken,
  since the order they happened within the window is unknowable.
- **Margin is approximated at 20% of notional.** Real SPAN plus exposure margin
  varies by stock and volatility.
- Costs are not modelled: no brokerage, slippage, STT or impact.

## Notes

- OI uses the **NSE last-Tuesday monthly** stock expiry (not weeklies, not BSE Thursday). On that Tuesday it rolls to next month.
- Angel One reports OI in shares; the scanner divides by lot size so the numbers
  match the contract counts shown on a trading terminal
- Quotes are batched 50 at a time at 1 request/second; candles run at 3/second,
  both within Angel One's documented limits, with backoff on rate-limit errors
- The instrument master and daily closes are cached in `.cache/` and refreshed daily
- Alerts have a cooldown (default 60 min) to avoid repeats
- Run `python main.py --once` outside market hours and you will get last traded values
