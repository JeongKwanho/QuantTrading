"""
Downtrend Channel Bounce Strategy — Version 1

Entry  : candle.low  <= lower_now  AND  candle.close > lower_now  → BUY  (market)
         RR 조건: (upper_now - close) / (close - low) >= min_rr

SL v1  : candle.low  <= entry_candle_low                           → SELL all (market)
TP1    : candle.high >= tp1_price (upper_now at entry)             → SELL 50% (market)
         → stop loss moved to avg_price
SL v2  : candle.low  <= avg_price  (after TP1)                     → SELL all (market)
TP2    : candle.high >= H2                                         → SELL all (market)

포지션 완전 청산 시 채널을 unlock → 다음 채널 탐지 시작.
"""

from patterns.trend_line import TrendChannel, TrendLinePattern
from strategies.base import BaseStrategy, FillEvent, MarketData, Signal


class TrendChannelV1(BaseStrategy):
    """하락 추세선 반등 매매 v1."""

    name = "trend_channel_v1"
    parameters = {
        "window": 50,
        "pivot_k": 2,
        "min_rr": 2.0,
        "cooldown": 5,       # SL 후 재진입 금지 캔들 수
    }

    def __init__(self, leverage: int = 1, **kwargs) -> None:
        super().__init__(leverage, **kwargs)
        self._pattern = TrendLinePattern(
            window=self.parameters["window"],
            pivot_k=self.parameters["pivot_k"],
        )
        self._cooldown_remaining: int = 0
        self._reset_position()

    # ── 포지션 상태 초기화 ─────────────────────────────────────────────

    def _reset_position(self) -> None:
        self._in_position:  bool  = False
        self._position_qty: float = 0.0
        self._avg_price:    float = 0.0
        self._sl_price:     float = 0.0   # 진입봉 최저가 → TP1 이후 평단가
        self._tp1_done:     bool  = False
        self._tp1_price:    float = 0.0   # 진입 시점 upper_now (고정)
        self._h2_price:     float = 0.0   # 진입 시점 H2 가격 (고정)

    # ── 메인 ──────────────────────────────────────────────────────────

    def on_data(self, data: MarketData) -> list[Signal]:
        self._pattern.evaluate(data)
        ch = self._pattern.downtrend_channel

        if not self._in_position:
            if self._cooldown_remaining > 0:
                self._cooldown_remaining -= 1
                return []
            return self._check_entry(data, ch)
        return self._check_exit(data)

    def on_fill(self, fill: FillEvent) -> None:
        if fill.direction == "BUY":
            total_cost = self._avg_price * self._position_qty + fill.price * fill.quantity
            self._position_qty += fill.quantity
            self._avg_price = total_cost / self._position_qty

        elif fill.direction == "SELL":
            self._position_qty = max(0.0, self._position_qty - fill.quantity)
            if self._position_qty <= 0.0:
                self._avg_price = 0.0
                # 포지션 완전 청산 → 채널 해제, 다음 채널 탐지 시작
                self._pattern.unlock()

    def on_stop(self) -> None:
        self._pattern.reset()
        self._reset_position()

    # ── 진입 조건 ─────────────────────────────────────────────────────

    def _check_entry(self, data: MarketData, ch: TrendChannel | None) -> list[Signal]:
        if ch is None:
            return []

        touched_lower = data.low   <= ch.lower_now
        closed_above  = data.close >  ch.lower_now
        closed_inside = data.close <  ch.upper_now
        if not (touched_lower and closed_above and closed_inside):
            return []

        risk   = data.close - data.low
        reward = ch.upper_now - data.close
        if risk <= 0 or reward / risk < self.parameters["min_rr"]:
            return []

        self._in_position = True
        self._sl_price    = data.low
        self._tp1_done    = False
        self._tp1_price   = ch.upper_now   # 진입 시점 상단선 (고정 목표)
        self._h2_price    = ch.l2_price    # H2 가격 (TP2 목표, 고정)

        return [Signal(
            symbol        = data.symbol,
            direction     = "BUY",
            quantity      = 0.0,
            price         = None,
            strategy_name = self.name,
            timestamp     = data.timestamp,
            metadata      = {
                "reason": "lower_bounce",
                "sl":  self._sl_price,
                "tp1": self._tp1_price,
                "tp2": self._h2_price,
            },
        )]

    # ── 청산 조건 ─────────────────────────────────────────────────────

    def _check_exit(self, data: MarketData) -> list[Signal]:
        if self._position_qty <= 0.0:
            return []

        # ── 1순위: 손절 ──────────────────────────────────────────────
        if data.low <= self._sl_price:
            qty = self._position_qty
            self._in_position = False
            self._cooldown_remaining = self.parameters["cooldown"]
            return [Signal(
                symbol        = data.symbol,
                direction     = "SELL",
                quantity      = qty,
                price         = None,
                strategy_name = self.name,
                timestamp     = data.timestamp,
                metadata      = {"reason": "stop_loss", "sl_price": self._sl_price},
            )]

        # ── 2순위: TP2 — H2 도달 (TP1 완료 후) ─────────────────────
        if self._tp1_done and data.high >= self._h2_price:
            qty = self._position_qty
            self._in_position = False
            return [Signal(
                symbol        = data.symbol,
                direction     = "SELL",
                quantity      = qty,
                price         = None,
                strategy_name = self.name,
                timestamp     = data.timestamp,
                metadata      = {"reason": "tp2_h2", "h2_price": self._h2_price},
            )]

        # ── 3순위: TP1 — 진입 시점 상단선 도달 ──────────────────────
        if not self._tp1_done and data.high >= self._tp1_price:
            sell_qty       = round(self._position_qty * 0.5, 8)
            self._tp1_done = True
            self._sl_price = self._avg_price
            return [Signal(
                symbol        = data.symbol,
                direction     = "SELL",
                quantity      = sell_qty,
                price         = None,
                strategy_name = self.name,
                timestamp     = data.timestamp,
                metadata      = {"reason": "tp1_upper", "new_sl": self._sl_price},
            )]

        return []
