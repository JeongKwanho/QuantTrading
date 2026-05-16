from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Signal:
    symbol: str
    direction: OrderSide
    quantity: float
    price: float | None = None          # None → MARKET order
    strategy_name: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)

    @property
    def order_type(self) -> OrderType:
        return OrderType.LIMIT if self.price is not None else OrderType.MARKET


@dataclass
class Balance:
    asset: str
    free: float
    locked: float

    @property
    def total(self) -> float:
        return self.free + self.locked


@dataclass
class Position:
    symbol: str
    side: OrderSide
    size: float
    entry_price: float
    unrealized_pnl: float = 0.0
    leverage: int = 1


@dataclass
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None
    status: OrderStatus
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: float
    fee: float
    fee_asset: str
    timestamp: datetime = field(default_factory=datetime.utcnow)


class BaseBroker(ABC):

    def __init__(self) -> None:
        self._on_fill_callback: Callable[[Fill], None] | None = None
        self._on_ticker_callback: Callable[[dict], None] | None = None

    # ── 콜백 등록 ─────────────────────────────────────────────────────────

    def set_on_fill(self, callback: Callable[[Fill], None]) -> None:
        self._on_fill_callback = callback

    def set_on_ticker(self, callback: Callable[[dict], None]) -> None:
        self._on_ticker_callback = callback

    # ── 연결 관리 ─────────────────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> None:
        """Establish REST session and start WebSocket stream."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close all connections cleanly."""

    # ── 계좌 정보 ─────────────────────────────────────────────────────────

    @abstractmethod
    async def get_balances(self) -> list[Balance]:
        """Return current asset balances."""

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Return open positions (futures only; spot returns empty list)."""

    # ── 주문 관리 ─────────────────────────────────────────────────────────

    @abstractmethod
    async def place_order(self, signal: Signal) -> Order:
        """Send an order to the exchange and return the resulting Order."""

    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an open order. Returns True if successful."""

    @abstractmethod
    async def get_order(self, symbol: str, order_id: str) -> Order:
        """Fetch a single order by ID."""

    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Return all open orders, optionally filtered by symbol."""

    # ── 시장 데이터 ───────────────────────────────────────────────────────

    @abstractmethod
    async def get_ticker(self, symbol: str) -> dict:
        """Return latest price info: {symbol, price, bid, ask, timestamp}."""

    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 10) -> dict:
        """Return orderbook: {bids: [[price, qty], ...], asks: [...]}."""

    @abstractmethod
    async def get_ohlcv(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
    ) -> list[dict]:
        """Return OHLCV candles: [{open, high, low, close, volume, timestamp}]."""
