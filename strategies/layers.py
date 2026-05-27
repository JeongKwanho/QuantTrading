from dataclasses import dataclass, field
from typing import Literal

from strategies.base import BaseStrategy, MarketData, Signal


Direction = Literal["BUY", "SELL"] | None


@dataclass
class LayerResult:
    """Evaluation result for one timeframe layer."""

    direction: Direction
    timeframe: str
    confirmed: bool
    signal: Signal | None = None
    signals: list[Signal] = field(default_factory=list)


class TimeframeLayer:
    """Bundle multiple strategies for one timeframe.

    The layer confirms a direction only when every emitted strategy signal points
    the same way. It also returns the raw signals so group-level execution can
    preserve strategy metadata such as SL/TP targets.
    """

    def __init__(self, timeframe: str, strategies: list[BaseStrategy]) -> None:
        self.timeframe = timeframe
        self.strategies = strategies
        self._confirmed_direction: Direction = None

    @property
    def confirmed_direction(self) -> Direction:
        return self._confirmed_direction

    def evaluate(self, data: MarketData) -> LayerResult:
        directions: list[str] = []
        layer_signals: list[Signal] = []

        for strategy in self.strategies:
            signals = strategy.on_data(data)
            for sig in signals:
                if sig.direction in ("BUY", "SELL"):
                    directions.append(sig.direction)
                    layer_signals.append(sig)

        if not directions:
            return LayerResult(
                direction=self._confirmed_direction,
                timeframe=self.timeframe,
                confirmed=self._confirmed_direction is not None,
            )

        if all(direction == directions[0] for direction in directions):
            self._confirmed_direction = directions[0]  # type: ignore
        else:
            self._confirmed_direction = None

        return LayerResult(
            direction=self._confirmed_direction,
            timeframe=self.timeframe,
            confirmed=self._confirmed_direction is not None,
            signal=layer_signals[0] if self._confirmed_direction is not None else None,
            signals=layer_signals,
        )

    def reset(self) -> None:
        self._confirmed_direction = None

    def to_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "strategies": [strategy.name for strategy in self.strategies],
            "confirmed_direction": self._confirmed_direction,
        }
