# OI + RSI Stock Scanner

Scans NSE F&O stocks on **live broker data** and alerts when:

1. **Call OI alert** — RSI is **at or above 70** and price is **near the highest Call OI strike**
2. **Put OI alert** — RSI is **at or below 35** and price is **near the highest Put OI strike**

## Data providers

Either broker supplies live prices *and* live option-chain OI from a single
authenticated session. **Angel One is the default because its market data is free.**

| Provider | Config value | Data cost | OI source |
|----------|--------------|-----------|-----------|
| **Angel One** | `angelone` | Free — no subscription, historical data included | Chain rebuilt from instrument master + full quotes |
| **Dhan** | `dhan` | ₹499 + taxes/month Data API plan | Native option chain endpoint |

Dhan bills Trading APIs and Data APIs separately: trading is free, but the live feed,
historical candles and option chain this scanner relies on all sit behind the paid
Data API plan. Angel One SmartAPI charges nothing for the same endpoints.

Switch with `data.provider` in `config.yaml`, or per run with `--provider`.

## Setup

```bash
cd "OI with stocks"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Option A — Angel One (default, free)

1. Create an app at [smartapi.angelone.in](https://smartapi.angelone.in) to get an API key
2. Enable TOTP and save the secret shown with the QR code
3. Fill in `.env`:
   ```
   ANGEL_API_KEY=your_api_key
   ANGEL_CLIENT_CODE=your_client_code
   ANGEL_PIN=your_pin
   ANGEL_TOTP_SECRET=your_totp_secret
   ```

Login happens automatically on every run using the TOTP secret, so there is no daily step.

From 1 Apr 2026 Angel One requires a registered static IP, but only for **order
execution**. This scanner is read-only and places no orders, so it is unaffected.

### Option B — Dhan (paid data plan)

1. Subscribe to **DhanHQ Data APIs** (₹499 + taxes/month) — the free Trading API tier
   does not include the live feed, historical candles or option chain
2. Open [web.dhan.co](https://web.dhan.co) → **DhanHQ Trading APIs** → generate an access token
3. Fill in `.env`:
   ```
   DHAN_CLIENT_ID=your_client_id
   DHAN_ACCESS_TOKEN=your_access_token
   ```
4. Set `provider: dhan` in `config.yaml`

The token is valid for 30 days, so there is no daily login step.

## Configuration

Edit `config.yaml`:

| Setting | Default | Description |
|---------|---------|-------------|
| `rsi.call_threshold` | 70 | RSI must be at or above this for a Call OI alert |
| `rsi.put_threshold` | 35 | RSI must be at or below this for a Put OI alert |
| `oi.proximity_pct` | 2.0 | Price must be within this % of the max OI strike |
| `data.provider` | angelone | `angelone` (free) or `dhan` (paid data plan) |
| `data.history_days` | 120 | Daily candles pulled for the RSI calculation |
| `watchlist` | 10 stocks | NSE F&O symbols to scan |
| `schedule.interval_minutes` | 15 | How often to scan during market hours |

## Usage

**Single scan of the whole watchlist:**
```bash
python main.py --once
```

**Scan one stock:**
```bash
python main.py --once --symbol RELIANCE
```

**Use the other broker for one run:**
```bash
python main.py --once --provider angelone
```

**Run on a schedule (every 15 min during market hours):**
```bash
python main.py
```

## Telegram notifications

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Get your chat ID (e.g. via [@userinfobot](https://t.me/userinfobot))
3. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```
4. Set `notifications.telegram: true` in `config.yaml`

## How it works

```
For each stock in watchlist:
  ├── Live option chain (nearest expiry) → max Call OI strike, max Put OI strike, spot LTP
  ├── Daily candles + live LTP          → 14-period RSI, refreshed intraday
  └── Check:
        RSI >= 70 and price near max Call OI strike → CALL OI ALERT
        RSI <= 35 and price near max Put OI strike → PUT OI ALERT
```

The last daily close is replaced with the live LTP before computing RSI, so the
indicator updates through the trading session instead of freezing at yesterday's close.

## Notes

- Only the **nearest expiry** is used for the OI comparison
- Dhan limits the option chain to **1 request every 3 seconds**; the scanner throttles itself
- Angel One quotes are batched 50 tokens at a time with a 1 request/second pause
- Instrument masters are cached in `.cache/` and refreshed once a day
- Alerts have a cooldown (default 60 min) to avoid repeats
- Run `python main.py --once` outside market hours and you will get the last traded values
