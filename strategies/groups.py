from typing import Literal

from strategies.base import MarketData, Signal
from strategies.layers import TimeframeLayer, Direction


# 지원하는 시간봉 목록
LARGE_TIMEFRAMES  = ["1d", "1w", "3d"]
MEDIUM_TIMEFRAMES = ["4h", "6h", "8h", "12h"]
SMALL_TIMEFRAMES  = ["1m", "3m", "5m", "15m", "30m", "1h"]


class BaseGroup:
    """
    모든 전략 그룹의 공통 베이스.
    on_candle()으로 시간봉 데이터를 받고,
    최종 Signal이 확정되면 반환한다.
    """
    trade_type: Literal["scalping", "swing", "position"] = "swing"

    @property
    def timeframes(self) -> list[str]:
        """이 그룹에 필요한 모든 시간봉 목록 (큰 → 작은 순서)."""
        raise NotImplementedError

    @property
    def primary_timeframe(self) -> str:
        """가장 작은(빠른) 시간봉 — 백테스트 equity curve 기준."""
        return self.timeframes[-1]

    def all_strategies(self):
        """그룹 내 모든 전략 인스턴스를 반환 (on_fill 연결 등에 사용)."""
        raise NotImplementedError

    def on_candle(self, timeframe: str, data: MarketData) -> list[Signal]:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

    def to_dict(self) -> dict:
        raise NotImplementedError

    def _build_signal(self, data: MarketData, direction: Direction) -> list[Signal]:
        if direction is None:
            return []
        return [Signal(
            symbol=data.symbol,
            direction=direction,
            quantity=0.0,        # 실제 수량은 리스크 매니저 또는 전략이 결정
            price=None,          # 시장가
            strategy_name=f"{self.trade_type}_group",
            timestamp=data.timestamp,
            metadata={"trade_type": self.trade_type},
        )]


class ScalpingGroup(BaseGroup):
    """
    단타: 큰봉 → 중간봉 → 작은봉 순서로 3단계 필터링.
    작은봉에서 신호가 나올 때, 위 두 레이어가 모두 같은 방향으로 확정되어야 한다.
    """
    trade_type = "scalping"

    def __init__(
        self,
        large: TimeframeLayer,
        medium: TimeframeLayer,
        small: TimeframeLayer,
    ) -> None:
        assert large.timeframe in LARGE_TIMEFRAMES,  f"large timeframe must be one of {LARGE_TIMEFRAMES}"
        assert medium.timeframe in MEDIUM_TIMEFRAMES, f"medium timeframe must be one of {MEDIUM_TIMEFRAMES}"
        assert small.timeframe in SMALL_TIMEFRAMES,  f"small timeframe must be one of {SMALL_TIMEFRAMES}"
        self.large  = large
        self.medium = medium
        self.small  = small

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
            small_result  = self.small.evaluate(data)
            medium_result = self.medium.confirmed_direction
            large_result  = self.large.confirmed_direction

            # 3레이어 전부 같은 방향일 때만 Signal 발생
            if (
                small_result.confirmed
                and small_result.direction == medium_result == large_result
            ):
                return self._build_signal(data, small_result.direction)

        return []

    def reset(self) -> None:
        self.large.reset()
        self.medium.reset()
        self.small.reset()

    def to_dict(self) -> dict:
        return {
            "trade_type": self.trade_type,
            "layers": {
                "large":  self.large.to_dict(),
                "medium": self.medium.to_dict(),
                "small":  self.small.to_dict(),
            },
        }


class SwingGroup(BaseGroup):
    """
    스윙: 큰봉 → 중간봉 순서로 2단계 필터링.
    중간봉에서 신호가 나올 때, 큰봉이 같은 방향으로 확정되어야 한다.
    """
    trade_type = "swing"

    def __init__(
        self,
        large: TimeframeLayer,
        medium: TimeframeLayer,
    ) -> None:
        assert large.timeframe in LARGE_TIMEFRAMES,  f"large timeframe must be one of {LARGE_TIMEFRAMES}"
        assert medium.timeframe in MEDIUM_TIMEFRAMES, f"medium timeframe must be one of {MEDIUM_TIMEFRAMES}"
        self.large  = large
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
            large_result  = self.large.confirmed_direction

            if (
                medium_result.confirmed
                and medium_result.direction == large_result
            ):
                return self._build_signal(data, medium_result.direction)

        return []

    def reset(self) -> None:
        self.large.reset()
        self.medium.reset()

    def to_dict(self) -> dict:
        return {
            "trade_type": self.trade_type,
            "layers": {
                "large":  self.large.to_dict(),
                "medium": self.medium.to_dict(),
            },
        }


class PositionGroup(BaseGroup):
    """
    장타: 큰봉 하나만으로 판단.
    """
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
                return self._build_signal(data, result.direction)
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


# ── 그룹 빌더 (프론트엔드 설정 → 그룹 생성) ────────────────────────────────

def build_group(config: dict, strategy_registry: dict[str, type]) -> BaseGroup:
    """
    프론트엔드에서 전달한 config dict로 그룹을 생성한다.

    config 형식:
    {
        "trade_type": "scalping",
        "layers": {
            "large":  {"timeframe": "1d",  "strategies": ["trend_follow", "volume_filter"]},
            "medium": {"timeframe": "4h",  "strategies": ["macd_cross"]},
            "small":  {"timeframe": "15m", "strategies": ["rsi_entry", "breakout"]},
        }
    }

    strategy_registry: {"strategy_name": StrategyClass, ...}
    """

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
            large  = _make_layer(layers["large"],  leverage),
            medium = _make_layer(layers["medium"], leverage),
            small  = _make_layer(layers["small"],  leverage),
        )
    elif trade_type == "swing":
        return SwingGroup(
            large  = _make_layer(layers["large"],  leverage),
            medium = _make_layer(layers["medium"], leverage),
        )
    elif trade_type == "position":
        return PositionGroup(
            large = _make_layer(layers["large"], leverage),
        )
    else:
        raise ValueError(f"Unknown trade_type: {trade_type}")
