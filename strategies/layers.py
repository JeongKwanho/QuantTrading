from dataclasses import dataclass, field
from typing import Literal

from strategies.base import BaseStrategy, MarketData, Signal


Direction = Literal["BUY", "SELL"] | None


@dataclass
class LayerResult:
    """TimeframeLayer 평가 결과."""
    direction: Direction    # "BUY" / "SELL" / None
    timeframe: str
    confirmed: bool         # 이 레이어가 현재 신호를 확정했는지


class TimeframeLayer:
    """
    하나의 시간봉에 속한 전략 묶음.
    모든 전략이 같은 방향을 반환해야 해당 방향으로 확정된다.
    """

    def __init__(self, timeframe: str, strategies: list[BaseStrategy]) -> None:
        self.timeframe = timeframe
        self.strategies = strategies
        self._confirmed_direction: Direction = None   # 마지막으로 확정된 방향

    @property
    def confirmed_direction(self) -> Direction:
        return self._confirmed_direction

    def evaluate(self, data: MarketData) -> LayerResult:
        """
        캔들 데이터를 받아 모든 전략을 실행.
        전부 같은 방향 → 확정 / 하나라도 다르면 → None 유지.
        """
        directions: list[str] = []

        for strategy in self.strategies:
            signals = strategy.on_data(data)
            for sig in signals:
                if sig.direction in ("BUY", "SELL"):
                    directions.append(sig.direction)

        if not directions:
            # 어떤 전략도 신호 없음 → 이전 확정 방향 유지
            return LayerResult(
                direction=self._confirmed_direction,
                timeframe=self.timeframe,
                confirmed=self._confirmed_direction is not None,
            )

        # 모든 신호가 동일한 방향인지 확인
        if all(d == directions[0] for d in directions):
            self._confirmed_direction = directions[0]  # type: ignore
        else:
            # 방향이 엇갈리면 확정 해제
            self._confirmed_direction = None

        return LayerResult(
            direction=self._confirmed_direction,
            timeframe=self.timeframe,
            confirmed=self._confirmed_direction is not None,
        )

    def reset(self) -> None:
        """확정 상태 초기화."""
        self._confirmed_direction = None

    def to_dict(self) -> dict:
        """API 응답용 직렬화."""
        return {
            "timeframe": self.timeframe,
            "strategies": [s.name for s in self.strategies],
            "confirmed_direction": self._confirmed_direction,
        }
