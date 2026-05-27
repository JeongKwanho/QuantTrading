from typing import Literal

from strategies.base import MarketData, Signal
from strategies.layers import Direction, TimeframeLayer


LARGE_TIMEFRAMES = ["1d", "1w", "3d"]
MEDIUM_TIMEFRAMES = ["4h", "6h", "8h", "12h"]
SMALL_TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h"]


class BaseGroup:
    """Common base for multi-timeframe strategy groups."""

    trade_type: Literal["scalping", "swing", "position"] = "swing"

    @property
    def timeframes(self) -> list[str]:
        raise NotImplementedError

    @property
    def primary_timeframe(self) -> str:
        return self.timeframes[-1]

    def all_strategies(self):
        raise NotImplementedError

    def on_candle(self, timeframe: str, data: MarketData) -> list[Signal]:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

    def to_dict(self) -> dict:
        raise NotImplementedError

    def _build_signal(
        self,
        data: MarketData,
        direction: Direction,
        source_signal: Signal | None = None,
    ) -> list[Signal]:
        if direction is None:
            return []

        metadata = {"trade_type": self.trade_type}
        if source_signal is not None:
            metadata.update(source_signal.metadata)
            metadata["trade_type"] = self.trade_type
            metadata["source_strategy"] = source_signal.strategy_name
            metadata["source_timestamp"] = source_signal.timestamp.isoformat()

        return [Signal(
            symbol=source_signal.symbol if source_signal is not None else data.symbol,
            direction=direction,
            quantity=source_signal.quantity if source_signal is not None else 0.0,
            price=source_signal.price if source_signal is not None else None,
            strategy_name=f"{self.trade_type}_group",
            timestamp=source_signal.timestamp if source_signal is not None else data.timestamp,
            metadata=metadata,
        )]


class ScalpingGroup(BaseGroup):
    """Three-layer confirmation: large, medium, then small timeframe trigger."""

    trade_type = "scalping"

    def __init__(
        self,
        large: TimeframeLayer,
        medium: TimeframeLayer,
        small: TimeframeLayer,
    ) -> None:
        assert large.timeframe in LARGE_TIMEFRAMES, f"large timeframe must be one of {LARGE_TIMEFRAMES}"
        assert medium.timeframe in MEDIUM_TIMEFRAMES, f"medium timeframe must be one of {MEDIUM_TIMEFRAMES}"
        assert small.timeframe in SMALL_TIMEFRAMES, f"small timeframe must be one of {SMALL_TIMEFRAMES}"
        self.large = large
        self.medium = medium
        self.small = small

    @property
    def timeframes(self) -> list[str]:
        return [self.large.timeframe, self.medium.timeframe, self.small.timeframe]

    def all_strategies(self):
        return self.large.strategies + self.medium.strategies + self.small.strategies

    def on_candle(self, timeframe: str, data: MarketData) -> list[Signal]:
        if timeframe == self.large.timeframe:
            self.large.evaluate(data)

        elif timeframe == self.medium.timeframe:
            self.medium.evaluate(data)

        elif timeframe == self.small.timeframe:
            small_result = self.small.evaluate(data)
            medium_result = self.medium.confirmed_direction
            large_result = self.large.confirmed_direction

            if (
                small_result.confirmed
                and small_result.direction == medium_result == large_result
            ):
                return self._build_signal(data, small_result.direction, small_result.signal)

        return []

    def reset(self) -> None:
        self.large.reset()
        self.medium.reset()
        self.small.reset()

    def to_dict(self) -> dict:
        return {
            "trade_type": self.trade_type,
            "layers": {
                "large": self.large.to_dict(),
                "medium": self.medium.to_dict(),
                "small": self.small.to_dict(),
            },
        }


class SwingGroup(BaseGroup):
    """Two-layer confirmation: large timeframe context, medium trigger."""

    trade_type = "swing"

    def __init__(
        self,
        large: TimeframeLayer,
        medium: TimeframeLayer,
    ) -> None:
        assert large.timeframe in LARGE_TIMEFRAMES, f"large timeframe must be one of {LARGE_TIMEFRAMES}"
        assert medium.timeframe in MEDIUM_TIMEFRAMES, f"medium timeframe must be one of {MEDIUM_TIMEFRAMES}"
        self.large = large
        self.medium = medium

    @property
    def timeframes(self) -> list[str]:
        return [self.large.timeframe, self.medium.timeframe]

    def all_strategies(self):
        return self.large.strategies + self.medium.strategies

    def on_candle(self, timeframe: str, data: MarketData) -> list[Signal]:
        if timeframe == self.large.timeframe:
            self.large.evaluate(data)

        elif timeframe == self.medium.timeframe:
            medium_result = self.medium.evaluate(data)
            large_result = self.large.confirmed_direction

            if medium_result.confirmed and medium_result.direction == large_result:
                return self._build_signal(data, medium_result.direction, medium_result.signal)

        return []

    def reset(self) -> None:
        self.large.reset()
        self.medium.reset()

    def to_dict(self) -> dict:
        return {
            "trade_type": self.trade_type,
            "layers": {
                "large": self.large.to_dict(),
                "medium": self.medium.to_dict(),
            },
        }


class PositionGroup(BaseGroup):
    """Single large-timeframe group."""

    trade_type = "position"

    def __init__(self, large: TimeframeLayer) -> None:
        assert large.timeframe in LARGE_TIMEFRAMES, f"large timeframe must be one of {LARGE_TIMEFRAMES}"
        self.large = large

    @property
    def timeframes(self) -> list[str]:
        return [self.large.timeframe]

    def all_strategies(self):
        return self.large.strategies

    def on_candle(self, timeframe: str, data: MarketData) -> list[Signal]:
        if timeframe == self.large.timeframe:
            result = self.large.evaluate(data)
            if result.confirmed:
                return self._build_signal(data, result.direction, result.signal)
        return []

    def reset(self) -> None:
        self.large.reset()

    def to_dict(self) -> dict:
        return {
            "trade_type": self.trade_type,
            "layers": {
                "large": self.large.to_dict(),
            },
        }


def build_group(config: dict, strategy_registry: dict[str, type]) -> BaseGroup:
    """Build a group from config."""

    def _make_layer(cfg: dict, leverage: int = 1) -> TimeframeLayer:
        strategies = [
            strategy_registry[name](leverage=leverage)
            for name in cfg["strategies"]
        ]
        return TimeframeLayer(timeframe=cfg["timeframe"], strategies=strategies)

    leverage = config.get("leverage", 1)
    trade_type = config["trade_type"]
    layers = config["layers"]

    if trade_type == "scalping":
        return ScalpingGroup(
            large=_make_layer(layers["large"], leverage),
            medium=_make_layer(layers["medium"], leverage),
            small=_make_layer(layers["small"], leverage),
        )
    if trade_type == "swing":
        return SwingGroup(
            large=_make_layer(layers["large"], leverage),
            medium=_make_layer(layers["medium"], leverage),
        )
    if trade_type == "position":
        return PositionGroup(
            large=_make_layer(layers["large"], leverage),
        )
    raise ValueError(f"Unknown trade_type: {trade_type}")
