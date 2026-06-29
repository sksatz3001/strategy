# Delta Bot Platform

A milestone-built proprietary-style trading platform with Python backend and Next.js dashboard.

## Completed Milestones

### Milestone 1: Backend foundation

- Delta login endpoint with paper-mode fallback
- Live candle stream engine
- Balance and open positions APIs
- Order placement/cancellation
- Logging, database bootstrap, scheduler

### Milestone 2: Strategy engine

Implemented in `backend/strategy.py`:

- Session starts at 00:00 New York time
- Waits for first 4H range
- Stores high/low
- Watches 5m candles for breakout
- Waits for re-entry into boundary buffer
- Emits trade signal with entry, SL, TP

### Milestone 3: Risk and execution

Implemented in `backend/risk.py` + orchestration in `backend/main.py`:

- 1% risk model configurable from environment
- Daily loss cap
- Max trades per day
- Max leverage guard
- Position sizing from stop distance

### Milestone 4: Dashboard

Implemented in `dashboard/`:

- Next.js 15 app router dashboard
- Live websocket updates from backend (`/ws/dashboard`)
- Metrics, symbol status, open positions, equity curve, trade history
- TradingView Lightweight Charts for equity visualization

### Milestone 5: Analytics and journaling

Implemented in `backend/analytics.py`, `backend/journal.py`, `backend/screenshots.py`:

- Trade history and equity curve APIs
- Heatmap API
- Trade replay API
- Weekly AI-style summary API
- Auto screenshot generation on entry (`chart.png`) and exit (`result.png`)

## Project Structure

```
delta-bot/
  backend/
  dashboard/
  database/
  docker-compose.yml
```

## Core Backend APIs

- `GET /health`
- `POST /auth/login`
- `GET /account/balance`
- `GET /positions/open`
- `POST /orders`
- `DELETE /orders/{order_id}`
- `GET /candles/latest/{symbol}`
- `GET /candles/history/{symbol}`
- `GET /strategy/status`
- `GET /strategies`
- `PUT /strategies/{name}`
- `GET /dashboard/overview`
- `GET /dashboard/equity`
- `GET /dashboard/trades`
- `GET /dashboard/heatmap`
- `GET /journal/events`
- `GET /journal/weekly-ai-summary`
- `GET /replay/{trade_id}`
- `POST /backtest/run`
- `POST /backtest/optimize`
- `WS /ws/candles/{symbol}`
- `WS /ws/dashboard`

## Local Run

1. Copy `.env.example` to `.env`
2. Backend:

   ```bash
   pip install -r requirements.txt
   uvicorn backend.main:app --reload
   ```

3. Dashboard:

   ```bash
   cd dashboard
   npm install
   npm run dev
   ```

## Docker Run

```bash
docker compose up
```

## Single-Container Docker Run (Safe VPS Coexistence)

This project includes an isolated single-container option that runs backend + dashboard together:

- Compose file: `docker-compose.single.yml`
- Dockerfile: `Dockerfile.single`
- Host ports: `18100` (API), `13100` (Dashboard)

Run it without touching existing stacks:

```bash
docker compose -f docker-compose.single.yml -p strategy_single up -d --build
```

Stop only this stack:

```bash
docker compose -f docker-compose.single.yml -p strategy_single down
```

## Live Trading Safety Controls

Added in `.env`:

- `LIVE_TRADING_ENABLED=false` (default, blocks all live order execution)
- `REQUIRE_LIVE_EXCHANGE_WHEN_ENABLED=true`

Behavior:

- If live trading is disabled, strategy/manual live orders are blocked.
- If live trading is enabled but Delta auth remains paper mode and `REQUIRE_LIVE_EXCHANGE_WHEN_ENABLED=true`, backend startup fails safe.

## Telegram Bot Setup

1. Open Telegram and message `@BotFather`
2. Run `/newbot`
3. Set bot name + username, then copy the bot token
4. Message your bot once (e.g. `hello`)
5. Get your chat id:

   ```bash
   curl "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates"
   ```

6. Put values in `.env`:

   - `TELEGRAM_ENABLED=true`
   - `TELEGRAM_BOT_TOKEN=<YOUR_TOKEN>`
   - `TELEGRAM_CHAT_ID=<YOUR_CHAT_ID>`

## Domain + HTTPS (Nginx, Non-Disruptive)

Use a new server block and new domain/subdomain so existing apps remain untouched.

1. DNS:

   - Add `A` record for `trade.yourdomain.com` to your VPS public IP

2. Nginx config (new file):

   ```nginx
   server {
       listen 80;
       server_name trade.yourdomain.com;

       location / {
           proxy_pass http://127.0.0.1:13100;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }

       location /api/ {
           proxy_pass http://127.0.0.1:18100/;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

3. Enable + validate:

   ```bash
   sudo ln -s /etc/nginx/sites-available/strategy-single.conf /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl reload nginx
   ```

4. TLS:

   ```bash
   sudo certbot --nginx -d trade.yourdomain.com
   ```

## Production Checklist

- Set `LIVE_TRADING_ENABLED=false` first and verify paper flow
- Confirm dashboard live updates and trade journaling
- Validate Telegram notifications
- Rotate exchange keys before going live if keys were ever shared
- Set `LIVE_TRADING_ENABLED=true` only after full paper validation

## Notes

- `backend/exchange.py` is now normalized to one implementation (duplicate concatenation removed).
- In this environment, Python/Docker runtime validation was not possible; static checks passed.
