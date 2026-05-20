"""
Uptrend Channel Bounce Strategy — Version 2  (SHORT)

Entry  : candle.high >= upper_now  AND  candle.close < upper_now  → SELL short (market)
         closed_inside : candle.close > lower_now
         RR 조건 : (close - lower_now) / (high - close) >= min_rr

SL v1  : candle.high >= entry_candle_high                         → BUY all  (market, at sl_price)
TP1    : candle.low  <= lower_now (at entry)                      → BUY 50%  (market)
         → stop loss moved to avg_price
SL v2  : candle.high >= avg_price  (after TP1)                    → BUY all  (market, at sl_price)
TP2    : candle.low  <= L2 price                                  → BUY all  (market)

포지션 완전 청산 시 채널 unlock → 다음 채널 탐지 시작.
"""

from patterns.trend_line import TrendLinePatternUp, TrendChannel
from strategies.base import BaseStrategy, FillEvent, MarketData, Signal


class TrendChannelV2(BaseStrategy):
    """상승 추세선 반등 공매도 매매 v2."""

    name = "trend_channel_v2"
    parameters = {
        "window": 50,
        "pivot_k": 2,
        "min_rr": 2.0,
        "cooldown": 5,
    }

    def __init__(self, leverage: int = 1, **kwargs) -> None:
        super().__init__(leverage, **kwargs)
        self._pattern = TrendLinePatternUp(
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
        self._sl_price:     float = 0.0   # 진입봉 고점 → TP1 이후 평단가
        self._tp1_done:     bool  = False
        self._tp1_price:    float = 0.0   # 진입 시점 lower_now (고정 목표)
        self._tp2_price:    float = 0.0   # L2 저점 (TP2 목표, 고정)

    # ── 메인 ──────────────────────────────────────────────────────────

    def on_data(self, data: MarketData) -> list[Signal]:
        self._pattern.evaluate(data)
        ch = self._pattern.uptrend_channel

        if not self._in_position:
            if self._cooldown_remaining > 0:
                self._cooldown_remaining -= 1
                return []
            return self._check_entry(data, ch)
        return self._check_exit(data, ch)

    def on_fill(self, fill: FillEvent) -> None:
        if fill.direction == "SELL":   # 숏 진입
            total_cost = self._avg_price * self._position_qty + fill.price * fill.quantity
            self._position_qty += fill.quantity
            self._avg_price = total_cost / self._position_qty
            self._pattern.freeze()  # 포지션 중 채널 고정

        elif fill.direction == "BUY":  # 숏 청산
            self._position_qty = max(0.0, self._position_qty - fill.quantity)
            if self._position_qty <= 0.0:
                self._avg_price = 0.0
                self._pattern.unlock()  # 전량 청산 시 채널 해제

    def on_stop(self) -> None:
        self._pattern.reset()
        self._reset_position()

    # ── 진입 조건 ─────────────────────────────────────────────────────

    def _check_entry(self, data: MarketData, ch: TrendChannel | None) -> list[Signal]:
        if ch is None:
            return []

        touched_upper = data.high  >= ch.upper_now
        closed_below  = data.close <  ch.upper_now
        closed_inside = data.close >  ch.lower_now
        if not (touched_upper and closed_below and closed_inside):
            return []

        risk   = data.high  - data.close     # 진입가 ~ SL 거리
        reward = data.close - ch.lower_now   # 진입가 ~ TP1 거리
        if risk <= 0 or reward / risk < self.parameters["min_rr"]:
            return []

        self._in_position = True
        self._sl_price    = data.high         # 진입봉 고점 (SL v1)
        self._tp1_done    = False
        self._tp1_price   = ch.lower_now      # 진입 시점 하단선 (고정)
        self._tp2_price   = ch.l2_price       # L2 저점 (TP2 목표, 고정)

        return [Signal(
            symbol        = data.symbol,
            direction     = "SELL",
            quantity      = 0.0,
            price         = None,
            strategy_name = self.name,
            timestamp     = data.timestamp,
            metadata      = {
                "reason": "upper_bounce",
                "sl":  self._sl_price,
                "tp1": self._tp1_price,
                "tp2": self._tp2_price,
            },
        )]

    # ── 청산 조건 ─────────────────────────────────────────────────────

    def _check_exit(self, data: MarketData, ch: TrendChannel | None) -> list[Signal]:
        if self._position_qty <= 0.0:
            return []

        # ── 1순위: 손절 (v1 진입봉 고점, v2 TP1 이후 평단가) ─────────
        if data.high >= self._sl_price:
            qty = self._position_qty
            self._in_position = False
            self._cooldown_remaining = self.parameters["cooldown"]
            return [Signal(
                symbol        = data.symbol,
                direction     = "BUY",
                quantity      = qty,
                price         = None,
                strategy_name = self.name,
                timestamp     = data.timestamp,
                metadata      = {"reason": "stop_loss", "sl_price": self._sl_price},
            )]

        # ── 2순위: TP2 — L2 저점 도달 (TP1 완료 후) ─────────────────
        if self._tp1_done and data.low <= self._tp2_price:
            qty = self._position_qty
            self._in_position = False
            return [Signal(
                symbol        = data.symbol,
                direction     = "BUY",
                quantity      = qty,
                price         = None,
                strategy_name = self.name,
                timestamp     = data.timestamp,
                metadata      = {"reason": "tp2_l2", "l2_price": self._tp2_price},
            )]

        # ── 3순위: TP1 — 현재 시점 하단 연장선 터치 ─────────────────
        current_lower = ch.lower_now if ch is not None else self._tp1_price
        if not self._tp1_done and data.low <= current_lower:
            buy_qty        = round(self._position_qty * 0.5, 8)
            self._tp1_done = True
            self._sl_price = self._avg_price   # SL을 평단가로 이동
            return [Signal(
                symbol        = data.symbol,
                direction     = "BUY",
                quantity      = buy_qty,
                price         = None,
                strategy_name = self.name,
                timestamp     = data.timestamp,
                metadata      = {"reason": "tp1_lower", "tp1_price": current_lower, "new_sl": self._sl_price},
            )]

        return []
