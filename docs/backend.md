# Backend Design

## Overview

The backend is responsible for data ingestion, signal processing, order management, risk control, and broker integration.
It runs as a standalone Python process and exposes a REST + WebSocket API for the frontend.

All inter-module communication flows through the internal event bus.
Strategies never access the broker or database directly — they only return `Signal` objects.

---

## Directory Structure

```
backend/
├── core/
│   ├── engine.py          # Main event loop — coordinates data, strategies, OMS
│   ├── event.py           # Internal event bus
│   └── scheduler.py       # APScheduler wrapper for market session management
├── data/
│   ├── fetcher.py         # REST historical OHLCV fetcher (warm-up & backtest)
│   ├── stream.py          # WebSocket real-time feed (tick / candle)
│   └── preprocessor.py    # Indicator calculation, data normalization
├── broker/
│   ├── base.py            # Abstract broker interface
│   ├── kis.py             # Korea Investment & Securities (KIS) adapter
│   └── mock.py            # Paper trading mock broker (no real money)
├── oms/
│   ├── order.py           # Order data class (MARKET / LIMIT / STOP)
│   ├── manager.py         # Order lifecycle: PENDING → SUBMITTED → FILLED / CANCELLED
│   └── position.py        # Open position tracker, unrealized P&L
├── risk/
│   ├── manager.py         # Pre-trade & post-trade risk gate
│   ├── stop_loss.py       # Stop-loss / take-profit execution
│   └── drawdown.py        # Portfolio drawdown monitor & trading halt
├── db/
│   ├── models.py          # SQLAlchemy ORM models
│   ├── session.py         # DB session factory
│   └── redis_client.py    # Redis cache client
└── api/
    ├── main.py            # FastAPI application entry point
    ├── routers/
    │   ├── portfolio.py   # Portfolio status endpoints
    │   ├── orders.py      # Order management endpoints
    │   ├── strategy.py    # Strategy control endpoints
    │   └── ws.py          # WebSocket endpoints (real-time push)
    └── schemas.py         # Pydantic request / response schemas
```

---

## Modules

### 1. Core Engine (`core/`)

- Runs the main async event loop.
- Subscribes strategies to market data events; calls each strategy's `on_data()` and routes returned `Signal` objects to the OMS.
- `scheduler.py` wraps APScheduler: fires `market_open`, `pre_market`, `market_close`, and `rebalance` events at configured times, which trigger `on_start()` / `on_stop()` on each active strategy.

**Data flow:**
```
stream.py → engine.py → strategy.on_data() → Signal → risk/manager.py → oms/manager.py → broker
                                                          ↑ blocked if risk check fails
```

### 2. Data Layer (`data/`)

- **fetcher.py** — pulls historical OHLCV via REST for backtest warm-up and indicator pre-computation.
- **stream.py** — maintains a WebSocket connection; emits `MarketData` events into the event bus.
- **preprocessor.py** — computes technical indicators (MA, EMA, RSI, Bollinger Bands, MACD, etc.) and normalizes into a unified `MarketData` dataclass consumed by strategies.

### 3. Broker Adapter (`broker/`)

All brokers implement `BaseBroker`:

```python
class BaseBroker:
    def place_order(self, order: Order) -> str: ...       # returns order_id
    def cancel_order(self, order_id: str) -> bool: ...
    def get_balance(self) -> dict: ...
    def get_positions(self) -> list[Position]: ...
```

- `kis.py` — wraps the KIS Open Trading API.
- `mock.py` — simulates fills at market price with configurable slippage; used for paper trading and backtesting.

### 4. Order Management System (`oms/`)

- Manages full order lifecycle: `PENDING → SUBMITTED → FILLED / PARTIALLY_FILLED / CANCELLED`.
- Tracks all open positions and calculates unrealized P&L in real time.
- On fill, emits a `FillEvent` back to the originating strategy via `on_fill()`.

### 5. Risk Manager (`risk/`)

- **Pre-trade checks** (run before every order is sent to broker):
  - Max position size per symbol
  - Daily loss limit
  - Max number of concurrent open orders
- **Post-trade monitoring** (runs on every tick):
  - Stop-loss / take-profit: submits close orders when thresholds are hit
  - Drawdown monitor: halts all strategies when portfolio drawdown exceeds configured threshold

### 6. Database (`db/`)

| Store | Technology | Purpose |
|-------|-----------|---------|
| Persistent | PostgreSQL | Trade history, order logs, daily P&L snapshots |
| Cache | Redis | Latest market data, current positions, strategy state |

### 7. API Server (`api/`)

Built with **FastAPI**. Consumed by the frontend dashboard.

#### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolio` | Current holdings and P&L |
| GET | `/orders` | Open and recent order list |
| POST | `/orders/cancel/{id}` | Cancel a specific order |
| GET | `/strategy` | List of loaded strategies and their state |
| POST | `/strategy/{name}/toggle` | Enable or disable a strategy |
| POST | `/strategy/{name}/params` | Update strategy parameters |
| GET | `/risk` | Current risk metrics (drawdown, daily loss) |

#### WebSocket Endpoints

| Path | Description |
|------|-------------|
| `/ws/market` | Real-time price feed |
| `/ws/portfolio` | Real-time P&L and position updates |
| `/ws/alerts` | Risk alerts and fill notifications |

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| API Framework | FastAPI |
| Task Scheduling | APScheduler |
| Database (persistent) | PostgreSQL |
| Database (cache) | Redis |
| ORM | SQLAlchemy |
| Data Processing | pandas, numpy |
| Technical Indicators | pandas-ta |
| WebSocket | websockets / aiohttp |
| Config & Validation | pydantic, python-dotenv |

---

## Security

- All API keys, broker credentials, and secrets are stored in `.env`.
- `.env` is listed in `.gitignore` and is **never committed to the repository**.
- `.env.example` (placeholder values only) is provided for reference.
- No credentials are hardcoded anywhere in the source.

---

## Environment Variables

See `config/.env.example` for the full list. Structure:

```
# Broker
KIS_APP_KEY=your_app_key_here
KIS_APP_SECRET=your_app_secret_here
KIS_ACCOUNT_NO=your_account_number_here

# Database
POSTGRES_URL=postgresql://user:password@localhost:5432/quantdb
REDIS_URL=redis://localhost:6379

# API Server
API_HOST=0.0.0.0
API_PORT=8000
```

---

## Dependency Rules

- `strategies/` never imports from `backend/broker/`, `backend/oms/`, or `backend/db/`.
- `frontend/` only communicates with `backend/api/` via HTTP / WebSocket.
- All intra-backend communication goes through `core/event.py`.
- `backtest/` uses `broker/mock.py` — never the live broker adapters.
