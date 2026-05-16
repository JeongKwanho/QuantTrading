from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

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


class StrategyStatus(str, Enum):
    ON = "ON"
    OFF = "OFF"
    ERROR = "ERROR"


class StrategyTimeframe(str, Enum):
    SCALPING = "scalping"
    SWING = "swing"
    POSITION = "position"


# ── Balance ───────────────────────────────────────────────────────────────────

class BalanceSchema(BaseModel):
    asset: str
    free: float
    locked: float
    total: float


# ── Position ──────────────────────────────────────────────────────────────────

class PositionSchema(BaseModel):
    symbol: str
    side: OrderSide
    size: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    leverage: int


# ── Portfolio ─────────────────────────────────────────────────────────────────

class PortfolioSchema(BaseModel):
    total_balance: float              # 총 자산 (USDT)
    available_balance: float          # 사용 가능 잔고
    unrealized_pnl: float             # 미실현 손익 합계
    daily_pnl: float                  # 오늘 실현 손익
    daily_pnl_pct: float              # 오늘 수익률 (%)
    positions: list[PositionSchema]
    updated_at: datetime


# ── Order ─────────────────────────────────────────────────────────────────────

class OrderSchema(BaseModel):
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None
    status: OrderStatus
    filled_qty: float
    avg_fill_price: float | None
    timestamp: datetime


class OrderListSchema(BaseModel):
    open_orders: list[OrderSchema]
    recent_orders: list[OrderSchema]  # 최근 체결/취소된 주문


# ── Order Request (POST) ──────────────────────────────────────────────────────

class PlaceOrderRequest(BaseModel):
    symbol: str = Field(..., example="BTCUSDT")
    side: OrderSide
    quantity: float = Field(..., gt=0)
    price: float | None = Field(None, description="None이면 시장가 주문")


class CancelOrderResponse(BaseModel):
    order_id: str
    success: bool
    message: str


# ── Strategy ──────────────────────────────────────────────────────────────────

class StrategySchema(BaseModel):
    name: str
    timeframe: StrategyTimeframe
    symbols: list[str]
    status: StrategyStatus
    parameters: dict
    last_signal_at: datetime | None
    description: str = ""


class StrategyListSchema(BaseModel):
    strategies: list[StrategySchema]
    total: int
    active_count: int


class ToggleStrategyResponse(BaseModel):
    name: str
    status: StrategyStatus
    message: str


class UpdateParamsRequest(BaseModel):
    parameters: dict = Field(..., description="업데이트할 파라미터 key-value")


class UpdateParamsResponse(BaseModel):
    name: str
    parameters: dict
    message: str


# ── Risk ──────────────────────────────────────────────────────────────────────

class RiskSchema(BaseModel):
    drawdown_pct: float               # 현재 드로다운 (%)
    drawdown_limit_pct: float         # 드로다운 한도 (%)
    daily_loss: float                 # 오늘 손실 (USDT)
    daily_loss_limit: float           # 일일 손실 한도 (USDT)
    open_order_count: int             # 미체결 주문 수
    max_open_orders: int              # 최대 허용 미체결 주문 수
    is_trading_halted: bool           # 전략 전체 정지 여부


# ── WebSocket 메시지 ──────────────────────────────────────────────────────────

class WsTickerMessage(BaseModel):
    type: str = "ticker"
    symbol: str
    price: float
    bid: float | None
    ask: float | None
    timestamp: datetime


class WsPortfolioMessage(BaseModel):
    type: str = "portfolio"
    total_balance: float
    unrealized_pnl: float
    daily_pnl: float
    positions: list[PositionSchema]
    timestamp: datetime


class WsAlertMessage(BaseModel):
    type: str = "alert"
    level: str                        # "INFO" / "WARNING" / "CRITICAL"
    message: str
    timestamp: datetime


# ── 공통 응답 ─────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: str = "ok"
    broker: str
    broker_connected: bool
    timestamp: datetime
