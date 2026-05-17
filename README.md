# QuantTrading

Automated quantitative trading system built in Python.

## Overview

A fully automated crypto futures trading system targeting Binance USDT-M Futures.
Strategies are composed from reusable pattern detection blocks (lego-style) and run across multiple timeframes simultaneously.

## Project Structure

```
QuantTrading/
├── backend/            # FastAPI server, broker adapters, API endpoints
│   ├── broker/         # BaseBroker, BinanceBroker, MockBroker
│   └── api/            # REST + WebSocket endpoints, state, schemas
├── strategies/         # Strategy framework
│   ├── base.py         # BaseStrategy, MarketData, Signal, FillEvent
│   ├── layers.py       # TimeframeLayer — AND logic across strategies
│   └── groups.py       # ScalpingGroup / SwingGroup / PositionGroup
├── patterns/           # Reusable pattern detection blocks (lego pieces)
│   ├── base.py         # BasePattern, PatternResult
│   └── trend_line.py   # TrendLinePattern — uptrend/downtrend channels
├── backtest/           # Backtesting engines
│   ├── historical.py   # Historical backtest (paginated REST fetch, multi-timeframe)
│   ├── live_paper.py   # Live paper trading (concurrent WebSocket streams)
│   └── report.py       # BacktestReport — return, drawdown, Sharpe, win rate
├── tools/              # Development utilities
│   └── chart_preview.py  # Interactive multi-timeframe browser chart
├── docs/               # Design documents
└── config/             # Configuration templates
```

## Architecture

### Strategy Composition

Patterns (lego blocks) are combined inside strategies. Strategies are grouped into timeframe layers, and layers form a group that produces trade signals.

```
Pattern (TrendLinePattern, ...)
  └─ Strategy (uses patterns to decide direction)
       └─ TimeframeLayer (AND logic — all strategies must agree)
            └─ StrategyGroup (ScalpingGroup / SwingGroup / PositionGroup)
                 └─ Signal → Broker → Order
```

### Multi-Timeframe Filtering

```
ScalpingGroup  (3 timeframes: 1d + 4h + 5m)
  large  (1d)  → all strategies agree? → confirmed_direction
  medium (4h)  → same
  small  (5m)  → same → all 3 match → Signal emitted

SwingGroup     (2 timeframes)
PositionGroup  (1 timeframe)
```

### Trend Channel Pattern (`patterns/trend_line.py`)

**Uptrend channel** (L1 < H1 < L2 on the X-axis):
- Lower trendline: slope defined by L1 → L2 (two lowest pivot lows)
- Upper trendline: parallel, passing through H1 (highest pivot high between L1 and L2)
- Only drawn when slope > 0 and all candles in [L1, L2] are contained within the channel

**Downtrend channel** (H1 < L1 < H2 on the X-axis):
- Upper trendline: slope defined by H1 → H2 (two highest pivot highs)
- Lower trendline: parallel, passing through L1 (lowest pivot low between H1 and H2)
- Only drawn when slope < 0 and all candles in [H1, H2] are contained within the channel

## Tools

### Chart Preview (`tools/chart_preview.py`)

Interactive browser-based multi-timeframe chart (1D / 4H / 5m) built with Plotly.js.

**Features:**
- Candlestick charts with configurable moving averages (MA5, MA20, MA50)
- Real-time trend channel overlay (green = uptrend, red = downtrend)
- L1, L2, H1 pivot point markers
- Per-chart date range and Y-axis range controls in the sidebar
- **Full-range scan mode**: slides the window across all history and marks every valid channel detection

**Run:**
```bash
# From repo root, using the QuantTrading conda env
python tools/chart_preview.py
```

## Setup

1. Clone the repository
2. Copy `config/.env.example` to `config/.env` and fill in your Binance API credentials
3. Create the conda environment and install dependencies:
   ```bash
   conda create -n QuantTrading python=3.11
   conda activate QuantTrading
   pip install -r requirements.txt
   ```
4. Run the backend API server:
   ```bash
   python -m backend.api.main
   ```
5. Open the chart preview tool:
   ```bash
   python tools/chart_preview.py
   ```

## Security

- API keys and secrets are stored in `config/.env` — **never committed**
- `.env`, `.mcp.json`, and `.claude/` are all in `.gitignore`
- The chart preview tool only reads market data (no order placement)

## License

Private repository — all rights reserved.
