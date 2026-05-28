"""
Downtrend channel bounce strategy, experimental branch.

This file starts as a clean copy of TrendChannelV1 so we can improve the idea
without changing the existing strategy.
"""

from detection.trend_line import TrendChannel, TrendLinePattern
from strategies.base import BaseStrategy, FillEvent, MarketData, Signal


class DecreaseTrendChannerV1(BaseStrategy):
    """Long-only bounce from the lower side of a downtrend channel."""

    name = "decrease_trend_channer_v1"
    parameters = {
        "window": 50,
        "pivot_k": 2,
        "min_rr": 2.0,
        "cooldown": 5,
        "atr_period": 14,
        "rsi_period": 14,
        "volume_period": 20,
        "min_channel_bars": 10,
        "min_channel_width_atr": 1.0,
        "max_channel_width_atr": 8.0,
        "min_slope_atr": 0.03,
        "min_down_leg_atr": 2.0,
        "min_lower_wick_ratio": 0.35,
        "max_lower_pierce_atr": 0.75,
        "recovery_buffer_atr": 0.05,
        "max_entry_rsi": 45.0,
        "min_volume_ratio": 1.0,
        "require_bull_market": False,
    }

    def __init__(self, leverage: int = 1, **kwargs) -> None:
        super().__init__(leverage, **kwargs)
        self._pattern = TrendLinePattern(
            window=self.parameters["window"],
            pivot_k=self.parameters["pivot_k"],
        )
        self._cooldown_remaining: int = 0
        self._reset_position()

    def _reset_position(self) -> None:
        self._in_position: bool = False
        self._position_qty: float = 0.0
        self._avg_price: float = 0.0
        self._sl_price: float = 0.0
        self._tp1_done: bool = False
        self._tp1_price: float = 0.0
        self._h2_price: float = 0.0

    def on_data(self, data: MarketData) -> list[Signal]:
        self._pattern.evaluate(data)
        channel = self._pattern.downtrend_channel

        if not self._in_position:
            if self._cooldown_remaining > 0:
                self._cooldown_remaining -= 1
                return []
            return self._check_entry(data, channel)

        return self._check_exit(data, channel)

    def on_fill(self, fill: FillEvent) -> None:
        if fill.direction == "BUY":
            total_cost = self._avg_price * self._position_qty + fill.price * fill.quantity
            self._position_qty += fill.quantity
            self._avg_price = total_cost / self._position_qty
            self._pattern.freeze()

        elif fill.direction == "SELL":
            self._position_qty = max(0.0, self._position_qty - fill.quantity)
            if self._position_qty <= 0.0:
                self._avg_price = 0.0
                self._pattern.unlock()

    def on_stop(self) -> None:
        self._pattern.reset()
        self._reset_position()

    def _check_entry(self, data: MarketData, channel: TrendChannel | None) -> list[Signal]:
        if channel is None:
            return []

        touched_lower = data.low <= channel.lower_now
        closed_above = data.close > channel.lower_now
        closed_inside = data.close < channel.upper_now
        if not (touched_lower and closed_above and closed_inside):
            return []

        atr = self._calc_atr(self.parameters["atr_period"])
        if atr <= 0.0:
            return []

        if not self._channel_quality_ok(channel, atr):
            return []
        if not self._touch_quality_ok(data, channel, atr):
            return []
        if not self._market_environment_ok(data):
            return []

        risk = data.close - data.low
        reward = channel.upper_now - data.close
        if risk <= 0 or reward / risk < self.parameters["min_rr"]:
            return []

        self._in_position = True
        self._sl_price = data.low
        self._tp1_done = False
        self._tp1_price = channel.upper_now
        self._h2_price = channel.l2_price

        return [Signal(
            symbol=data.symbol,
            direction="BUY",
            quantity=0.0,
            price=None,
            strategy_name=self.name,
            timestamp=data.timestamp,
            metadata={
                "reason": "lower_bounce",
                "sl": self._sl_price,
                "tp1": self._tp1_price,
                "tp2": self._h2_price,
            },
        )]

    def _check_exit(self, data: MarketData, channel: TrendChannel | None) -> list[Signal]:
        if self._position_qty <= 0.0:
            return []

        if data.low <= self._sl_price:
            quantity = self._position_qty
            self._in_position = False
            self._cooldown_remaining = self.parameters["cooldown"]
            return [Signal(
                symbol=data.symbol,
                direction="SELL",
                quantity=quantity,
                price=None,
                strategy_name=self.name,
                timestamp=data.timestamp,
                metadata={"reason": "stop_loss", "sl_price": self._sl_price},
            )]

        if self._tp1_done and data.high >= self._h2_price:
            quantity = self._position_qty
            self._in_position = False
            return [Signal(
                symbol=data.symbol,
                direction="SELL",
                quantity=quantity,
                price=None,
                strategy_name=self.name,
                timestamp=data.timestamp,
                metadata={"reason": "tp2_h2", "h2_price": self._h2_price},
            )]

        current_upper = channel.upper_now if channel is not None else self._tp1_price
        if not self._tp1_done and data.high >= current_upper:
            sell_quantity = round(self._position_qty * 0.5, 8)
            self._tp1_done = True
            self._sl_price = self._avg_price
            return [Signal(
                symbol=data.symbol,
                direction="SELL",
                quantity=sell_quantity,
                price=None,
                strategy_name=self.name,
                timestamp=data.timestamp,
                metadata={
                    "reason": "tp1_upper",
                    "tp1_price": current_upper,
                    "new_sl": self._sl_price,
                },
            )]

        return []

    def _channel_quality_ok(self, channel: TrendChannel, atr: float) -> bool:
        channel_bars = abs(channel.l2_idx - channel.l1_idx)
        if channel_bars < self.parameters["min_channel_bars"]:
            return False

        width_atr = channel.channel_gap / atr
        if width_atr < self.parameters["min_channel_width_atr"]:
            return False
        if width_atr > self.parameters["max_channel_width_atr"]:
            return False

        slope_atr = abs(channel.slope) / atr
        if slope_atr < self.parameters["min_slope_atr"]:
            return False

        down_leg = channel.l1_price - channel.h1_price
        if down_leg / atr < self.parameters["min_down_leg_atr"]:
            return False

        return True

    def _touch_quality_ok(self, data: MarketData, channel: TrendChannel, atr: float) -> bool:
        candle_range = data.high - data.low
        if candle_range <= 0.0:
            return False

        lower_wick = min(data.open, data.close) - data.low
        if lower_wick / candle_range < self.parameters["min_lower_wick_ratio"]:
            return False

        pierce_depth = max(0.0, channel.lower_now - data.low)
        if pierce_depth / atr > self.parameters["max_lower_pierce_atr"]:
            return False

        recovery = data.close - channel.lower_now
        if recovery / atr < self.parameters["recovery_buffer_atr"]:
            return False

        return True

    def _market_environment_ok(self, data: MarketData) -> bool:
        if self.parameters["require_bull_market"]:
            if not data.indicators.get("bull_market", False):
                return False

        rsi = self._calc_rsi(self.parameters["rsi_period"])
        if rsi is not None and rsi > self.parameters["max_entry_rsi"]:
            return False

        avg_volume = self._avg_volume(self.parameters["volume_period"])
        if avg_volume is not None:
            volume_ratio = data.volume / avg_volume if avg_volume > 0.0 else 0.0
            if volume_ratio < self.parameters["min_volume_ratio"]:
                return False

        return True

    def _calc_atr(self, period: int) -> float:
        history = self._pattern._history
        if len(history) < 2:
            return 0.0

        start = max(1, len(history) - period)
        true_ranges: list[float] = []
        for idx in range(start, len(history)):
            current = history[idx]
            previous = history[idx - 1]
            true_ranges.append(max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            ))

        return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

    def _calc_rsi(self, period: int) -> float | None:
        history = self._pattern._history
        if len(history) <= period:
            return None

        gains: list[float] = []
        losses: list[float] = []
        recent = history[-period - 1:]
        for idx in range(1, len(recent)):
            change = recent[idx].close - recent[idx - 1].close
            if change >= 0:
                gains.append(change)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(change))

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0.0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _avg_volume(self, period: int) -> float | None:
        history = self._pattern._history
        if len(history) <= period:
            return None

        previous = history[-period - 1:-1]
        if not previous:
            return None

        return sum(candle.volume for candle in previous) / len(previous)
