from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from strategies.base import MarketData


Direction = Literal["BUY", "SELL"] | None


@dataclass
class PatternResult:
    detected:  bool
    direction: Direction  # 패턴이 방향성을 가질 때 (없으면 None)
    strength:  float      # 0.0 ~ 1.0 (신호 강도)
    name:      str


class BasePattern(ABC):
    """
    단일 패턴 감지 블록.
    MarketData를 받아 패턴 감지 여부와 방향을 반환한다.
    전략(Strategy)은 여러 패턴을 조합해서 Signal을 만든다.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def evaluate(self, data: MarketData) -> PatternResult: ...

    def reset(self) -> None:
        """상태 초기화 (상태가 있는 패턴에서 오버라이드)."""
