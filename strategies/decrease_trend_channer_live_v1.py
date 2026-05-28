"""
Realtime-oriented downtrend pullback long strategy.

This is the live-safe version of DecreaseTrendChannerV1 / New+B:
- Requires a recent bull-market context.
- Does not mark a position as open until a BUY fill arrives.
- Does not mark exits as complete until SELL fills arrive.
- Uses close-based exits to avoid OHLC intrabar ordering assumptions.
"""

from detection.trend_line import TrendChannel, TrendLinePattern
from strategies.base import BaseStrategy, FillEvent, MarketData, Signal


class DecreaseTrendChannerLiveV1(BaseStrategy):
    """Long-only pullback buy from a downtrend channel inside a recent bull context."""

    name = "decrease_trend_channer_live_v1"
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
        "bull_fast_period": 5,
        "bull_slow_period": 20,
        "bull_grace_bars": 50,
    }

    def __init__(self, leverage: int = 1, **kwargs) -> None:
        super().__init__(leverage, **kwargs)
        self._pattern = TrendLinePattern(
            window=self.parameters["window"],
            pivot_k=self.parameters["pivot_k"],
        )
        self._cooldown_remaining = 0
        self._close_history: list[float] = []
        self._last_bull_idx: int | None = None
        self._bar_idx = -1
        self._pending_entry: dict | None = None
        self._pending_exit_reason: str | None = None
        self._reset_position()

    def _reset_position(self) -> None:
        self._in_position = False
        self._position_qty = 0.0
        self._avg_price = 0.0
        self._sl_price = 0.0
        self._tp1_done = False
        self._tp1_price = 0.0
        self._h2_price = 0.0

    def on_data(self, data: MarketData) -> list[Signal]:
        self._bar_idx += 1
        self._close_history.append(data.close)
        self._update_bull_state()

        self._pattern.evaluate(data)
        channel = self._pattern.downtrend_channel

        if self._pending_entry is not None or self._pending_exit_reason is not None:
            return []

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

            if self._pending_entry is not None:
                self._sl_price = self._pending_entry["sl"]
                self._tp1_done = False
                self._tp1_price = self._pending_entry["tp1"]
                self._h2_price = self._pending_entry["tp2"]
                self._pending_entry = None

            self._in_position = self._position_qty > 0.0
            self._pattern.freeze()

        elif fill.direction == "SELL":
            self._position_qty = max(0.0, self._position_qty - fill.quantity)
            reason = self._pending_exit_reason
            self._pending_exit_reason = None

            if self._position_qty <= 0.0:
                self._reset_position()
                if reason == "stop_loss":
                    self._cooldown_remaining = self.parameters["cooldown"]
                self._pattern.unlock()
                return

            if reason == "tp1_upper":
                self._tp1_done = True
                self._sl_price = self._avg_price

    def on_stop(self) -> None:
        self._pattern.reset()
        self._close_history.clear()
        self._last_bull_idx = None
        self._bar_idx = -1
        self._pending_entry = None
        self._pending_exit_reason = None
        self._cooldown_remaining = 0
        self._reset_position()

    def _check_entry(self, data: MarketData, channel: TrendChannel | None) -> list[Signal]:
        if channel is None:
            return []
        if not self._recent_bull_market():
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
        if risk <= 0.0 or reward / risk < self.parameters["min_rr"]:
            return []

        self._pending_entry = {
            "sl": data.low,
            "tp1": channel.upper_now,
            "tp2": channel.l2_price,
        }

        return [Signal(
            symbol=data.symbol,
            direction="BUY",
            quantity=0.0,
            price=None,
            strategy_name=self.name,
            timestamp=data.timestamp,
            metadata={
                "reason": "lower_bounce",
                "sl": self._pending_entry["sl"],
                "tp1": self._pending_entry["tp1"],
                "tp2": self._pending_entry["tp2"],
            },
        )]

    def _check_exit(self, data: MarketData, channel: TrendChannel | None) -> list[Signal]:
        if self._position_qty <= 0.0:
            return []

        if data.close <= self._sl_price:
            return self._sell_all(data, "stop_loss", {"sl_price": self._sl_price})

        if self._tp1_done and data.close >= self._h2_price:
            return self._sell_all(data, "tp2_h2", {"h2_price": self._h2_price})

        current_upper = channel.upper_now if channel is not None else self._tp1_price
        if not self._tp1_done and data.close >= current_upper:
            sell_quantity = round(self._position_qty * 0.5, 8)
            if sell_quantity <= 0.0:
                return []
            self._pending_exit_reason = "tp1_upper"
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
                    "new_sl": self._avg_price,
                },
            )]

        return []

    def _sell_all(self, data: MarketData, reason: str, metadata: dict) -> list[Signal]:
        quantity = self._position_qty
        if quantity <= 0.0:
            return []
        self._pending_exit_reason = reason
        payload = {"reason": reason}
        payload.update(metadata)
        return [Signal(
            symbol=data.symbol,
            direction="SELL",
            quantity=quantity,
            price=None,
            strategy_name=self.name,
            timestamp=data.timestamp,
            metadata=payload,
        )]

    def _update_bull_state(self) -> None:
        fast_period = self.parameters["bull_fast_period"]
        slow_period = self.parameters["bull_slow_period"]
        if len(self._close_history) < slow_period:
            return

        fast = sum(self._close_history[-fast_period:]) / fast_period
        slow = sum(self._close_history[-slow_period:]) / slow_period
        close = self._close_history[-1]
        if close > slow and fast > slow:
            self._last_bull_idx = self._bar_idx

    def _recent_bull_market(self) -> bool:
        if self._last_bull_idx is None:
            return False
        return self._bar_idx - self._last_bull_idx <= self.parameters["bull_grace_bars"]

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
