# Strategy Design

## Overview

All trading strategies share a common interface defined in `strategies/base.py`.
Strategies are isolated from broker and database layers — they only receive market data and return `Signal` objects.
The core engine dynamically loads all `BaseStrategy` subclasses at startup from the `strategies/` directory.

---

## Directory Structure

```
strategies/
├── base.py              # BaseStrategy, Signal, MarketData definitions
├── scalping/            # Short-term strategies (hold: minutes to hours)
│   └── __init__.py
├── swing/               # Swing strategies (hold: days to weeks)
│   └── __init__.py
└── position/            # Position strategies (hold: weeks to months)
    └── __init__.py
```

---

## Base Interface

### `MarketData`

Unified data object passed to every strategy on each tick/candle.

```python
@dataclass
class MarketData:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    indicators: dict        # pre-computed indicators from preprocessor.py
    orderbook: dict | None  # bid/ask levels (available for scalping strategies)
```

### `Signal`

Return type of `on_data()`. Represents a single trading intention.

```python
@dataclass
class Signal:
    symbol: str
    direction: Literal["BUY", "SELL", "CLOSE"]
    order_type: Literal["MARKET", "LIMIT", "STOP"]
    price: float | None     # required for LIMIT and STOP orders
    quantity: float
    strategy_name: str
    timestamp: datetime
    metadata: dict          # optional — for logging and analysis
```

### `BaseStrategy`

```python
class BaseStrategy(ABC):
    name: str                   # unique strategy identifier
    timeframe: str              # "scalping" | "swing" | "position"
    symbols: list[str]          # target symbols
    enabled: bool = True
    parameters: dict = {}       # user-adjustable parameters

    @abstractmethod
    def on_data(self, data: MarketData) -> list[Signal]:
        """Called on every new market data event. Return signals or empty list."""
        ...

    def on_fill(self, fill: FillEvent) -> None:
        """Called when an order originating from this strategy is filled."""
        ...

    def on_start(self) -> None:
        """Called at market open or when strategy is enabled."""
        ...

    def on_stop(self) -> None:
        """Called at market close or when strategy is disabled."""
        ...
```

---

## Strategy Categories

### Scalping (`strategies/scalping/`)

- **Target hold time**: minutes to tens of minutes
- **Data frequency**: tick or 1-minute candle
- **Orderbook access**: available (`MarketData.orderbook`)
- **Typical activation window**: 09:05 ~ 11:30, 13:00 ~ 15:20 (KRX)
- **Key parameters**: `entry_threshold`, `max_hold_minutes`, `position_size`

### Swing (`strategies/swing/`)

- **Target hold time**: days to weeks
- **Data frequency**: daily or 60-minute candle
- **Orderbook access**: not required
- **Typical activation window**: end of day signal generation, next open execution
- **Key parameters**: `lookback_period`, `stop_loss_pct`, `take_profit_pct`

### Position (`strategies/position/`)

- **Target hold time**: weeks to months
- **Data frequency**: daily or weekly candle
- **Orderbook access**: not required
- **Typical activation window**: rebalance schedule (weekly / monthly)
- **Key parameters**: `rebalance_interval`, `max_positions`, `allocation_method`

---

## Rules

1. A strategy must **never** import from `backend/broker/`, `backend/oms/`, or `backend/db/`.
2. A strategy must **never** call `place_order()` directly — return a `Signal` and let the engine route it.
3. A strategy must be **stateless across sessions** — all persistent state is stored via the DB layer by the engine, not the strategy itself.
4. Parameter validation must be done in `__init__()` so misconfiguration fails at startup, not mid-session.

---

## Adding a New Strategy

1. Create a new file under the appropriate category folder (e.g., `strategies/swing/my_strategy.py`).
2. Subclass `BaseStrategy` and implement `on_data()`.
3. The engine will auto-discover and register it on the next startup — no manual registration needed.

Example skeleton:

```python
from strategies.base import BaseStrategy, Signal, MarketData

class MySwingStrategy(BaseStrategy):
    name = "my_swing_strategy"
    timeframe = "swing"
    symbols = ["005930"]   # Samsung Electronics
    parameters = {
        "lookback": 20,
        "stop_loss_pct": 0.03,
    }

    def on_data(self, data: MarketData) -> list[Signal]:
        # implement entry / exit logic here
        return []
```
