"""Combined detection judge.

This module only detects market structures. It does not decide entries,
exits, position sizing, or order direction.
"""

from dataclasses import dataclass, field
from typing import Any

from detection.base import PatternResult
from detection.fair_value_gap import FairValueGap, FairValueGapPattern
from detection.order_block import OrderBlock, OrderBlockPattern
from detection.trend_line import TrendChannel, TrendLinePattern, TrendLinePatternUp
from strategies.base import MarketData


@dataclass
class DetectionItem:
    """One detection category result inside the recent lookback window."""

    name: str
    detected: bool
    direction: str | None = None
    strength: float = 0.0
    currently_detected: bool = False
    last_detected_bar: int | None = None
    bars_since_detected: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DetectionSnapshot:
    """Full detection state after evaluating one candle."""

    symbol: str
    timestamp: object
    minimum_required: int
    lookback_bars: int
    bar_index: int
    items: dict[str, DetectionItem]

    @property
    def detected_count(self) -> int:
        return sum(1 for item in self.items.values() if item.detected)

    @property
    def has_minimum_detections(self) -> bool:
        return self.detected_count >= self.minimum_required

    @property
    def all_three_detected(self) -> bool:
        return self.detected_count >= 3

    def detected_names(self) -> list[str]:
        return [name for name, item in self.items.items() if item.detected]


class CombinedDetectionJudge:
    """
    Evaluate all current detection modules together.

    Categories:
      - trend_line: downtrend channel and/or uptrend channel
      - order_block: bullish OB
      - fair_value_gap: bullish/bearish FVG

    The final count is based on a rolling candle window. It does not require all
    detections to occur on the same candle. If FVG, trend line, and OB each
    appear at least once within `lookback_bars`, `all_three_detected` is true.
    """

    parameters = {
        "minimum_required": 3,
        "lookback_bars": 15,
        "trend_window": 50,
        "trend_pivot_k": 2,
        "ob_window": 10,
        "ob_pivot_k": 2,
        "ob_trend_window": 30,
        "fvg_trend_window": 8,
        "fvg_pivot_k": 2,
        "fvg_min_gap_size": 0.0,
        "fvg_min_gap_pct": 0.001,
        "fvg_middle_range_multiplier": 1.2,
        "fvg_min_trend_candle_ratio": 0.55,
    }

    def __init__(self, **kwargs) -> None:
        self.parameters = dict(self.__class__.parameters)
        self.parameters.update(kwargs)

        self.downtrend = TrendLinePattern(
            window=self.parameters["trend_window"],
            pivot_k=self.parameters["trend_pivot_k"],
        )
        self.uptrend = TrendLinePatternUp(
            window=self.parameters["trend_window"],
            pivot_k=self.parameters["trend_pivot_k"],
        )
        self.order_block = OrderBlockPattern(
            window=self.parameters["ob_window"],
            pivot_k=self.parameters["ob_pivot_k"],
            trend_window=self.parameters["ob_trend_window"],
        )
        self.fair_value_gap = FairValueGapPattern(
            trend_window=self.parameters["fvg_trend_window"],
            pivot_k=self.parameters["fvg_pivot_k"],
            min_gap_size=self.parameters["fvg_min_gap_size"],
            min_gap_pct=self.parameters["fvg_min_gap_pct"],
            middle_range_multiplier=self.parameters["fvg_middle_range_multiplier"],
            min_trend_candle_ratio=self.parameters["fvg_min_trend_candle_ratio"],
        )
        self._bar_index = -1
        self._last_detected_bar: dict[str, int] = {}

    def evaluate(self, data: MarketData) -> DetectionSnapshot:
        self._bar_index += 1
        down_result = self.downtrend.evaluate(data)
        up_result = self.uptrend.evaluate(data)
        ob_result = self.order_block.evaluate(data)
        fvg_result = self.fair_value_gap.evaluate(data)

        items = {
            "trend_line": self._trend_item(down_result, up_result),
            "order_block": self._order_block_item(ob_result),
            "fair_value_gap": self._fair_value_gap_item(fvg_result),
        }
        return DetectionSnapshot(
            symbol=data.symbol,
            timestamp=data.timestamp,
            minimum_required=self.parameters["minimum_required"],
            lookback_bars=self.parameters["lookback_bars"],
            bar_index=self._bar_index,
            items=items,
        )

    def reset(self) -> None:
        self.downtrend.reset()
        self.uptrend.reset()
        self.order_block.reset()
        self.fair_value_gap.reset()
        self._bar_index = -1
        self._last_detected_bar.clear()

    def _trend_item(
        self,
        down_result: PatternResult,
        up_result: PatternResult,
    ) -> DetectionItem:
        down_channel = self.downtrend.downtrend_channel
        up_channel = self.uptrend.uptrend_channel
        currently_detected = down_channel is not None or up_channel is not None

        direction = None
        if down_channel is not None and up_channel is None:
            direction = "downtrend"
        elif up_channel is not None and down_channel is None:
            direction = "uptrend"
        elif down_channel is not None and up_channel is not None:
            direction = "mixed"

        recent_detected, last_bar, bars_since = self._recent_state(
            "trend_line",
            currently_detected,
        )

        return DetectionItem(
            name="trend_line",
            detected=recent_detected,
            direction=direction,
            strength=max(down_result.strength, up_result.strength),
            currently_detected=currently_detected,
            last_detected_bar=last_bar,
            bars_since_detected=bars_since,
            details={
                "downtrend": self._channel_details(down_channel),
                "uptrend": self._channel_details(up_channel),
            },
        )

    def _order_block_item(self, result: PatternResult) -> DetectionItem:
        ob = self.order_block.bullish_ob
        currently_detected = ob is not None and ob.valid
        recent_detected, last_bar, bars_since = self._recent_state(
            "order_block",
            currently_detected,
        )
        return DetectionItem(
            name="order_block",
            detected=recent_detected,
            direction="bullish" if currently_detected else None,
            strength=result.strength,
            currently_detected=currently_detected,
            last_detected_bar=last_bar,
            bars_since_detected=bars_since,
            details={"bullish_ob": self._order_block_details(ob)},
        )

    def _fair_value_gap_item(self, result: PatternResult) -> DetectionItem:
        fvg = self.fair_value_gap.last_fvg
        currently_detected = fvg is not None
        recent_detected, last_bar, bars_since = self._recent_state(
            "fair_value_gap",
            currently_detected,
        )
        return DetectionItem(
            name="fair_value_gap",
            detected=recent_detected,
            direction=fvg.direction if fvg is not None else None,
            strength=result.strength,
            currently_detected=currently_detected,
            last_detected_bar=last_bar,
            bars_since_detected=bars_since,
            details={"last_fvg": self._fvg_details(fvg)},
        )

    def _recent_state(
        self,
        name: str,
        currently_detected: bool,
    ) -> tuple[bool, int | None, int | None]:
        if currently_detected:
            self._last_detected_bar[name] = self._bar_index

        last_bar = self._last_detected_bar.get(name)
        if last_bar is None:
            return False, None, None

        bars_since = self._bar_index - last_bar
        return bars_since <= self.parameters["lookback_bars"], last_bar, bars_since

    def _channel_details(self, channel: TrendChannel | None) -> dict[str, Any] | None:
        if channel is None:
            return None
        return {
            "direction": channel.direction,
            "slope": channel.slope,
            "lower_now": channel.lower_now,
            "upper_now": channel.upper_now,
            "channel_gap": channel.channel_gap,
            "l1_idx": channel.l1_idx,
            "l1_price": channel.l1_price,
            "l2_idx": channel.l2_idx,
            "l2_price": channel.l2_price,
            "h1_idx": channel.h1_idx,
            "h1_price": channel.h1_price,
        }

    def _order_block_details(self, ob: OrderBlock | None) -> dict[str, Any] | None:
        if ob is None:
            return None
        return {
            "ob_open": ob.ob_open,
            "ob_close": ob.ob_close,
            "ob_high": ob.ob_high,
            "ob_low": ob.ob_low,
            "timestamp": ob.timestamp,
            "valid": ob.valid,
        }

    def _fvg_details(self, fvg: FairValueGap | None) -> dict[str, Any] | None:
        if fvg is None:
            return None
        return {
            "direction": fvg.direction,
            "trend": fvg.trend,
            "lower": fvg.lower,
            "upper": fvg.upper,
            "start_timestamp": fvg.start_timestamp,
            "middle_timestamp": fvg.middle_timestamp,
            "end_timestamp": fvg.end_timestamp,
            "gap_size": fvg.gap_size,
            "gap_pct": fvg.gap_pct,
            "middle_range": fvg.middle_range,
            "side_range_max": fvg.side_range_max,
            "filled": fvg.filled,
            "filled_timestamp": fvg.filled_timestamp,
        }
