# OI + RSI Stock Scanner

Scans NSE F&O stocks on **live Angel One data** and alerts when:

1. **Call OI alert** — RSI is **at or above 70** and price is **near the highest Call OI strike**
2. **Put OI alert** — RSI is **at or below 35** and price is **near the highest Put OI strike**

Live prices, RSI and option-chain OI all come from Angel One SmartAPI, which is free
with an Angel One account — no data subscription, historical data included.

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
| `rsi.call_threshold` | 70 | RSI must be at or above this for a Call OI alert |
| `rsi.put_threshold` | 35 | RSI must be at or below this for a Put OI alert |
| `oi.proximity_pct` | 2.0 | Price must be within this % of the max OI strike |
| `data.history_days` | 120 | Daily candles pulled for the RSI calculation |
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

**Scheduled mode (scans every 30 min, 09:15–15:45 on weekdays):**
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

## Telegram notifications

```bash
python scripts/setup_telegram.py
```

It asks for the bot token from [@BotFather](https://t.me/BotFather), finds your chat
ID automatically, sends a test message and writes both values to `.env`.

Each scan sends one grouped message rather than one per stock:

```
OI + RSI alerts — 10 Aug 2026 15:45

CALL OI (overbought, near max Call OI)
• TITAN: RSI 74.6 | ₹5,090.00 vs strike ₹5,100 (0.20% away)
    Call ΔOI +2,934 | Put ΔOI +2,992 | ΔPCR 1.02  (contracts)
• HAL: RSI 75.8 | ₹4,928.00 vs strike ₹5,000 (1.44% away)
    Call ΔOI +162 | Put ΔOI +46 | ΔPCR 0.28  (contracts)

PUT OI (oversold, near max Put OI)
• LICHSGFIN: RSI 30.0 | ₹500.00 vs strike ₹500 (0.00% away)
    Call ΔOI -410 | Put ΔOI -73  (contracts)
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

`.github/workflows/scan.yml` runs the scan on GitHub's servers every 30 minutes
from 09:15 to 15:45 IST on weekdays, so nothing needs to stay open at home.
It is free for public repositories.

1. Set up Telegram first — a hosted run has no terminal to print to
2. Push the credentials in `.env` up as repository secrets:
   ```bash
   ./scripts/sync_github_secrets.sh
   ```
   Re-run it whenever you rotate a credential. To do it by hand instead, add
   `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, `ANGEL_TOTP_SECRET`,
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` under
   **Settings → Secrets and variables → Actions**
3. Open the **Actions** tab, pick **OI + RSI scan**, and use **Run workflow** to
   trigger a scan by hand and confirm the alert reaches your phone

GitHub schedules are best effort, so a scan can start 5–20 minutes later than
listed during busy periods. Exchange holidays are not detected either, so a
holiday scan will report the previous session's values.

## How it works

```
1. Fetch live LTP for every stock      (batched 50 per request)
2. Compute RSI from cached daily closes + live LTP
3. Keep only stocks with RSI >= 70 or <= 35
4. For those candidates only, fetch the option chain
5. Alert when price sits near the max Call OI (high RSI)
   or max Put OI (low RSI) strike
```

Step 3 is what makes a full scan practical. Angel One's option chain has to be
rebuilt per stock from the instrument master, costing several requests each, so
fetching it for all 208 stocks would take around 17 minutes. Screening on RSI first
narrows it to a handful of candidates and cuts a full scan to about a minute.

Daily closes are fetched once per day and cached in `.cache/`, so only the first
run of the day pays for candles. RSI then uses the live LTP as today's close, which
keeps the indicator moving through the session instead of freezing at yesterday's
close.

Typical timings for the full 208-stock universe:

| Run | Time |
|-----|------|
| First scan of the day (fetches candles) | ~2m 20s |
| Later scans (candle cache warm) | ~1m |

## Open interest change and the change PCR

Alerts also carry how open interest moved since the previous session's close, at
the two peak strikes:

- **Call ΔOI** — change in *call* OI at the max Call strike, versus its previous
  close. Positive means fresh calls are being written there, negative means the
  strike is unwinding. **Put ΔOI** is the same for puts at the max Put strike.
  Each figure covers one leg at one strike; they are never summed together.
- **Change PCR** — ΔPut OI at the max Put strike divided by ΔCall OI at the max Call
  strike. Above 1 means puts are building faster than calls.

The ratio is deliberately **only shown when both sides are adding** positions. If
either side is unwinding, the division flips sign and stops meaning anything, so
the scanner shows the two signed changes and omits the ratio rather than printing
a misleading number.

Angel One's quote feed has no change-in-OI field, so this comes from its historical
OI endpoint, costing two extra requests per alerting stock. Those requests are only
made once a stock has already qualified, which keeps a full scan around a minute.

## Paper trading

Every alert is also taken as a simulated stock-futures trade, tracked in
`data/paper_book.json`. The strategy treats the signal as a **reversal**: the max
OI strikes are read as resistance and support.

| Signal | Position |
|--------|----------|
| RSI ≥ 70 near max Call OI | **SHORT** 2 lots — selling into resistance |
| RSI ≤ 35 near max Put OI | **LONG** 2 lots — buying at support |

Exits, checked on every scan:

| Rule | Action |
|------|--------|
| 6% move in our favour | Close 1 lot |
| 15% move in our favour | Close the remaining lot |
| 3% move against us | Close everything |
| Expiry reached | Close everything at market |

Settings live under `paper_trading` in `config.yaml` — capital, lot count, both
targets, the stop and the assumed margin.

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

- Only the **nearest expiry** is used for the OI comparison
- Angel One reports OI in shares; the scanner divides by lot size so the numbers
  match the contract counts shown on a trading terminal
- Quotes are batched 50 at a time at 1 request/second; candles run at 3/second,
  both within Angel One's documented limits, with backoff on rate-limit errors
- The instrument master and daily closes are cached in `.cache/` and refreshed daily
- Alerts have a cooldown (default 60 min) to avoid repeats
- Run `python main.py --once` outside market hours and you will get last traded values
