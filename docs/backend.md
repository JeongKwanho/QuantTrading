# Backend Design

## Overview

The backend is responsible for data ingestion, signal generation, order management, risk control, and broker integration.
It runs as a standalone process and exposes a REST API for the frontend.

---

## Directory Structure

```
backend/
├── core/
│   ├── engine.py          # Main event loop & strategy scheduler
│   ├── signal.py          # Signal data class
│   └── event.py           # Internal event bus
├── data/
│   ├── fetcher.py         # REST historical data fetcher
│   ├── stream.py          # WebSocket real-time feed
│   └── preprocessor.py    # OHLCV normalization, indicator calculation
├── broker/
│   ├── base.py            # Abstract broker interface
│   ├── kis.py             # Korea Investment & Securities (KIS) adapter
│   └── mock.py            # Paper trading mock broker
├── oms/
│   ├── order.py           # Order data class (market / limit / stop)
│   ├── manager.py         # Order lifecycle management
│   └── position.py        # Position tracker
├── risk/
│   ├── manager.py         # Pre-trade & post-trade risk checks
│   ├── stop_loss.py       # Stop-loss / take-profit logic
│   └── drawdown.py        # Drawdown monitor
├── db/
│   ├── models.py          # SQLAlchemy ORM models
│   ├── session.py         # DB session factory
│   └── redis_client.py    # Redis cache client
├── api/
│   ├── main.py            # FastAPI application entry point
│   ├── routers/
│   │   ├── portfolio.py   # Portfolio status endpoints
│   │   ├── orders.py      # Order management endpoints
│   │   └── strategy.py    # Strategy control endpoints
│   └── schemas.py         # Pydantic request/response schemas
└── config/
    ├── settings.py        # Loads environment variables
    └── logging.py         # Logging configuration
```

---

## Modules

### 1. Core Engine (`core/`)

- Runs the main event loop that coordinates data, strategies, and order execution.
- Strategies register themselves with the engine and receive market data events.
- The engine calls each strategy's `on_data()` method and routes returned signals to the OMS.

### 2. Data Layer (`data/`)

- **Fetcher**: pulls historical OHLCV data via REST for backtesting and warm-up.
- **Stream**: maintains a WebSocket connection for real-time tick / candle data.
- **Preprocessor**: computes technical indicators (MA, RSI, Bollinger Bands, etc.) and normalizes data into a unified DataFrame format.

### 3. Broker Adapter (`broker/`)

- All brokers implement the abstract `BaseBroker` interface:
  - `place_order()`, `cancel_order()`, `get_balance()`, `get_positions()`
- `kis.py` wraps the KIS Open API.
- `mock.py` simulates order fills for paper trading with no real money.

### 4. Order Management System (`oms/`)

- Manages the full lifecycle of an order: `PENDING → SUBMITTED → FILLED / CANCELLED`.
- Tracks open positions and calculates unrealized P&L.
- Emits fill events back to the strategy for post-fill logic.

### 5. Risk Manager (`risk/`)

- **Pre-trade checks**: position size limit, daily loss limit, max open orders.
- **Stop-loss / Take-profit**: monitors open positions tick-by-tick and submits close orders when thresholds are hit.
- **Drawdown monitor**: halts all trading when portfolio drawdown exceeds a configurable threshold.

### 6. Database (`db/`)

- **PostgreSQL**: stores trade history, order logs, and strategy performance snapshots.
- **Redis**: caches latest market data and position state for low-latency reads by the API.

### 7. API Server (`api/`)

- Built with **FastAPI**.
- Exposes REST endpoints consumed by the frontend dashboard.
- Key endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolio` | Current holdings and P&L |
| GET | `/orders` | Open and recent order list |
| POST | `/orders/cancel/{id}` | Cancel a specific order |
| GET | `/strategy` | List of loaded strategies and their state |
| POST | `/strategy/{name}/toggle` | Enable or disable a strategy |

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
| Data Processing | pandas, numpy, ta-lib |
| WebSocket | websockets / aiohttp |
| Config Management | python-dotenv |

---

## Security

- All API keys, broker credentials, and secrets are stored in a `.env` file.
- `.env` is listed in `.gitignore` and is **never committed to the repository**.
- A `.env.example` file (with placeholder values only) is provided for reference.
- No credentials are hardcoded anywhere in the source code.

---

## Environment Variables

See `.env.example` for the full list. Example structure:

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

## Notes

- Strategies live in the `strategies/` directory (outside `backend/`) and are loaded dynamically by the engine at startup.
- The backend does not import from `frontend/`.
- All inter-module communication within the backend goes through the internal event bus (`core/event.py`).
