# OI + RSI Stock Scanner

Scans NSE F&O stocks and sends alerts when:

1. **Call OI alert** — RSI is **at or above 70** and price is **near the highest Call OI strike**
2. **Put OI alert** — RSI is **at or below 35** and price is **near the highest Put OI strike**

## Setup

```bash
cd "OI with stocks"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional, for Telegram
```

## Configuration

Edit `config.yaml`:

| Setting | Default | Description |
|---------|---------|-------------|
| `rsi.call_threshold` | 70 | RSI must be at or above this for Call OI alert |
| `rsi.put_threshold` | 35 | RSI must be at or below this for Put OI alert |
| `oi.proximity_pct` | 2.0 | Price within this % of the max OI strike |
| `watchlist` | 10 stocks | NSE symbols to scan |
| `schedule.interval_minutes` | 15 | How often to scan during market hours |

## Usage

**Single scan (all watchlist stocks):**
```bash
python main.py --once
```

**Scan one stock:**
```bash
python main.py --once --symbol RELIANCE
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
  ├── Fetch daily prices → calculate 14-period RSI
  ├── Fetch NSE option chain → find max Call OI & max Put OI strikes
  └── Check:
        RSI >= 70 + price near max Call OI strike → CALL OI ALERT
        RSI ≤ 35  + price near max Put OI strike → PUT OI ALERT
```

## Notes

- Uses **nearest expiry** option chain from NSE
- NSE API may rate-limit; keep watchlist reasonable
- RSI is calculated from Yahoo Finance daily data
- OI data comes from NSE option chain API
- Alerts have a cooldown (default 60 min) to avoid spam
