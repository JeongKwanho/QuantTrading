from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class MarketData:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    indicators: dict = field(default_factory=dict)
    orderbook: dict | None = None


@dataclass
class FillEvent:
    order_id: str
    symbol: str
    direction: Literal["BUY", "SELL"]
    price: float
    quantity: float
    fee: float
    timestamp: datetime


@dataclass
class Signal:
    symbol: str
    direction: Literal["BUY", "SELL", "CLOSE"]
    quantity: float
    price: float | None = None          # None → 시장가
    strategy_name: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)


class BaseStrategy(ABC):

    name: str = ""
    timeframe: Literal["scalping", "swing", "position"] = "swing"
    symbols: list[str] = []
    parameters: dict = {}

    def __init__(self, leverage: int = 1, **kwargs) -> None:
        self.leverage = leverage
        self.enabled = True
        # parameters는 서브클래스 클래스 변수를 복사해서 인스턴스 변수로
        self.parameters = dict(self.__class__.parameters)
        self.parameters.update(kwargs)

    @abstractmethod
    def on_data(self, data: MarketData) -> list[Signal]:
        """새 캔들/틱 데이터가 올 때마다 호출. Signal 리스트 반환."""
        ...

    def on_fill(self, fill: FillEvent) -> None:
        """주문 체결 시 호출 (선택 구현)."""

    def on_start(self) -> None:
        """장 시작 또는 전략 활성화 시 호출 (선택 구현)."""

    def on_stop(self) -> None:
        """장 마감 또는 전략 비활성화 시 호출 (선택 구현)."""
