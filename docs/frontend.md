# Frontend Design

## Overview

The frontend is a real-time dashboard for monitoring portfolio status, controlling strategies, and viewing performance reports.
It communicates with the backend exclusively through the FastAPI server via REST and WebSocket.

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Framework | React 18 + TypeScript |
| Build Tool | Vite |
| Styling | Tailwind CSS |
| Charts | TradingView Lightweight Charts |
| Server State | TanStack Query (REST) |
| Real-time | Native WebSocket API |
| HTTP Client | Axios |

---

## Directory Structure

```
frontend/
├── src/
│   ├── pages/
│   │   ├── Dashboard.tsx        # Main overview page
│   │   ├── StrategyControl.tsx  # Strategy ON/OFF and parameter tuning
│   │   ├── Chart.tsx            # Candlestick chart with overlays
│   │   ├── RiskMonitor.tsx      # Real-time risk metrics
│   │   └── Reports.tsx          # Performance reports and backtest results
│   ├── components/
│   │   ├── PortfolioCard.tsx    # Total assets, daily P&L summary
│   │   ├── PositionTable.tsx    # Open positions table
│   │   ├── OrderTable.tsx       # Recent orders table
│   │   ├── StrategyCard.tsx     # Single strategy status card
│   │   ├── RiskBar.tsx          # Drawdown / daily loss progress bar
│   │   └── AlertBanner.tsx      # Risk alert notifications
│   ├── hooks/
│   │   ├── useWebSocket.ts      # Generic WebSocket connection hook
│   │   ├── usePortfolio.ts      # Portfolio state (TanStack Query + WS)
│   │   ├── useOrders.ts         # Order list state
│   │   └── useStrategies.ts     # Strategy list and control
│   ├── api/
│   │   ├── client.ts            # Axios instance with base URL config
│   │   ├── portfolio.ts         # Portfolio REST calls
│   │   ├── orders.ts            # Order REST calls
│   │   └── strategy.ts          # Strategy REST calls
│   ├── types/
│   │   └── index.ts             # Shared TypeScript type definitions
│   └── main.tsx                 # App entry point
├── public/
├── index.html
├── package.json
└── vite.config.ts
```

---

## Pages

### 1. Dashboard (Main)

- **Portfolio summary card**: total assets, daily P&L, total return rate
- **Open positions table**: symbol, quantity, average price, current price, unrealized P&L
- **Recent fills table**: time, symbol, direction, quantity, fill price
- **Active strategy list**: name, timeframe, status badge (ON / OFF / ERROR)
- Real-time updates via `/ws/portfolio`

### 2. Strategy Control

- List of all loaded strategies with ON/OFF toggle
- Per-strategy parameter editor (rendered from `strategy.parameters` schema)
- Recent signal log per strategy
- Calls `POST /strategy/{name}/toggle` and `POST /strategy/{name}/params`

### 3. Chart

- Candlestick chart powered by TradingView Lightweight Charts
- Symbol selector
- Timeframe selector (1m / 5m / 15m / 60m / 1D)
- Overlays: entry/exit markers, moving averages
- Real-time candle updates via `/ws/market`

### 4. Risk Monitor

- Current portfolio drawdown (gauge chart)
- Daily loss vs. daily limit (progress bar)
- Per-strategy position size vs. limit
- Alert history (populated from `/ws/alerts`)

### 5. Reports

- Period selector: daily / weekly / monthly / custom
- Equity curve chart
- Per-strategy performance table: win rate, profit factor, max drawdown, Sharpe ratio
- Backtest result viewer (load from DB via REST)

---

## Backend Communication

### REST (TanStack Query)

| Hook | Endpoint | Refresh |
|------|----------|---------|
| `usePortfolio` | GET `/portfolio` | 10s polling |
| `useOrders` | GET `/orders` | on-demand |
| `useStrategies` | GET `/strategy` | on-demand |

### WebSocket

| Channel | Endpoint | Data |
|---------|----------|------|
| Market feed | `/ws/market` | Real-time price tick / candle |
| Portfolio | `/ws/portfolio` | P&L and position updates |
| Alerts | `/ws/alerts` | Risk alerts, fill notifications |

WebSocket connections are managed by `useWebSocket.ts`, which handles:
- Auto-reconnect with exponential backoff
- Connection state (`CONNECTING / OPEN / CLOSED / ERROR`)
- Message deserialization

---

## Environment Variables

```
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_BASE_URL=ws://localhost:8000
```

---

## Dependency Rules

- `frontend/` only communicates with `backend/api/` — never imports backend Python modules.
- All API base URLs are configured via environment variables (`.env.local`), never hardcoded.
