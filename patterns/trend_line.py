from dataclasses import dataclass
from datetime import datetime

from patterns.base import BasePattern, PatternResult
from strategies.base import MarketData


# ── 채널 데이터 ───────────────────────────────────────────────────────────────

@dataclass
class TrendChannel:
    """
    두 평행선 채널. 3개 피벗 포인트로 구성.

    direction = "down":
        upper  ─── H1 → H2  (최고 피벗 고점 2개)
        lower  ─ ─ 동일 기울기, L1 (최저 피벗 저점) 통과

    필드 매핑 (downtrend):
      l1_idx/l1_price = H1 (첫 번째 고점)
      l2_idx/l2_price = H2 (두 번째 고점)
      h1_idx/h1_price = L1 (저점)
    """
    direction:   str    # "up" | "down"
    slope:       float
    lower_now:   float
    upper_now:   float
    channel_gap: float

    l1_idx:   int;  l1_price: float
    l2_idx:   int;  l2_price: float
    h1_idx:   int;  h1_price: float


# ── 패턴 ─────────────────────────────────────────────────────────────────────

class TrendLinePattern(BasePattern):
    """
    피벗 포인트로 하락 채널을 구성하고 잠근다.

    채널 이탈(봉마감 기준) 시 H2를 새 H1으로 carry → 연속 채널 탐지.
    포지션 완전 청산(unlock) 시에도 H2 carry.
    """

    def __init__(self, window: int = 50, pivot_k: int = 2, **kwargs) -> None:
        self.window  = window
        self.pivot_k = pivot_k
        self._history: list[MarketData] = []

        self.uptrend_channel:   TrendChannel | None = None
        self.downtrend_channel: TrendChannel | None = None

        # 잠금 상태
        self._locked:      bool  = False
        self._lck_upper:   float = 0.0
        self._lck_lower:   float = 0.0
        self._lck_slope:   float = 0.0
        self._lck_gap:     float = 0.0
        self._lck_channel: TrendChannel | None = None

        # H2 carry: 채널 이탈/청산 후 H2를 다음 탐지의 H1으로 재사용
        self._carry_ts:    datetime | None = None
        self._carry_price: float           = 0.0

    @property
    def name(self) -> str:
        return "trend_line"

    # ── 잠금 해제 (포지션 완전 청산) ─────────────────────────────────────────

    def unlock(self) -> None:
        """전략이 포지션 완전 청산 후 호출. H2를 새 H1으로 carry."""
        self._save_carry_from_locked()
        self._locked      = False
        self._lck_channel = None
        self.downtrend_channel = None

    # ── 메인 ─────────────────────────────────────────────────────────────────

    def evaluate(self, data: MarketData) -> PatternResult:
        self._history.append(data)
        if len(self._history) > self.window * 3:
            self._history = self._history[-self.window * 3:]

        n = len(self._history)

        # ── 잠금 모드 ────────────────────────────────────────────────────────
        if self._locked:
            self._lck_upper += self._lck_slope
            self._lck_lower += self._lck_slope

            if data.close > self._lck_upper or data.close < self._lck_lower:
                # 봉마감 채널 이탈 → H2 carry 후 무효화
                self._save_carry_from_locked()
                self._locked           = False
                self._lck_channel      = None
                self.downtrend_channel = None
            else:
                old = self._lck_channel
                self._lck_channel = TrendChannel(
                    direction   = old.direction,
                    slope       = self._lck_slope,
                    lower_now   = self._lck_lower,
                    upper_now   = self._lck_upper,
                    channel_gap = self._lck_gap,
                    l1_idx      = old.l1_idx, l1_price = old.l1_price,
                    l2_idx      = old.l2_idx, l2_price = old.l2_price,
                    h1_idx      = old.h1_idx, h1_price = old.h1_price,
                )
                self.downtrend_channel = self._lck_channel

            detected = self.downtrend_channel is not None
            return PatternResult(detected=detected, direction=None,
                                 strength=0.0, name=self.name)

        # ── 탐지 모드 ────────────────────────────────────────────────────────
        if n < self.pivot_k * 2 + 2:
            self.uptrend_channel   = None
            self.downtrend_channel = None
            return self._no_signal()

        pivot_lows  = self._find_pivots(is_low=True)
        pivot_highs = self._find_pivots(is_low=False)

        self.uptrend_channel   = self._build_uptrend_channel(pivot_lows, pivot_highs, n)
        self.downtrend_channel = self._build_downtrend_channel(pivot_lows, pivot_highs, n)

        if self.downtrend_channel is not None:
            ch = self.downtrend_channel
            self._locked      = True
            self._lck_upper   = ch.upper_now
            self._lck_lower   = ch.lower_now
            self._lck_slope   = ch.slope
            self._lck_gap     = ch.channel_gap
            self._lck_channel = ch
            self._carry_ts    = None  # 새 채널 확정 → carry 해제

        detected = self.uptrend_channel is not None or self.downtrend_channel is not None
        return PatternResult(detected=detected, direction=None,
                             strength=0.0, name=self.name)

    def reset(self) -> None:
        self._history.clear()
        self.uptrend_channel   = None
        self.downtrend_channel = None
        self._locked           = False
        self._lck_channel      = None
        self._carry_ts         = None
        self._carry_price      = 0.0

    # ── carry 저장 헬퍼 ──────────────────────────────────────────────────────

    def _save_carry_from_locked(self) -> None:
        """잠긴 채널의 H2(l2)를 다음 탐지의 H1으로 저장."""
        if self._lck_channel is None:
            return
        try:
            h2_data = self._history[self._lck_channel.l2_idx]
            self._carry_ts    = h2_data.timestamp
            self._carry_price = self._lck_channel.l2_price
        except (IndexError, AttributeError):
            self._carry_ts = None

    # ── 상승 채널 ────────────────────────────────────────────────────────────

    def _build_uptrend_channel(
        self,
        pivot_lows:  list[int],
        pivot_highs: list[int],
        n: int,
    ) -> TrendChannel | None:
        if len(pivot_lows) < 2 or len(pivot_highs) < 1:
            return None

        h1_idx   = max(pivot_highs, key=lambda i: self._history[i].high)
        h1_price = self._history[h1_idx].high

        lows_before = [i for i in pivot_lows if i < h1_idx]
        if not lows_before:
            return None
        l1_idx   = min(lows_before, key=lambda i: self._history[i].low)
        l1_price = self._history[l1_idx].low

        lows_after = [i for i in pivot_lows if i > h1_idx]
        if not lows_after:
            return None
        l2_idx   = min(lows_after, key=lambda i: self._history[i].low)
        l2_price = self._history[l2_idx].low

        slope = (l2_price - l1_price) / (l2_idx - l1_idx)
        if slope <= 0:
            return None

        lower_at_h1 = l1_price + slope * (h1_idx - l1_idx)
        channel_gap = h1_price - lower_at_h1
        if channel_gap <= 0:
            return None

        for i in range(l1_idx, l2_idx + 1):
            lower_i = l1_price + slope * (i - l1_idx)
            c = self._history[i]
            if c.low < lower_i or c.high > lower_i + channel_gap:
                return None

        lower_now = l1_price + slope * (n - 1 - l1_idx)
        upper_now = lower_now + channel_gap

        return TrendChannel(
            direction   = "up",
            slope       = slope,
            lower_now   = lower_now,
            upper_now   = upper_now,
            channel_gap = channel_gap,
            l1_idx      = l1_idx,  l1_price = l1_price,
            l2_idx      = l2_idx,  l2_price = l2_price,
            h1_idx      = h1_idx,  h1_price = h1_price,
        )

    # ── 하락 채널 ────────────────────────────────────────────────────────────

    def _build_downtrend_channel(
        self,
        pivot_lows:  list[int],
        pivot_highs: list[int],
        n: int,
    ) -> TrendChannel | None:
        # carry 모드: 이전 H2를 H1으로 고정, 이후 L1/H2만 탐색
        if self._carry_ts is not None:
            h1_idx = self._find_carry_in_history()
            if h1_idx is None:
                self._carry_ts = None  # history에서 사라짐 → carry 해제
            else:
                lows_after = [i for i in pivot_lows if i > h1_idx]
                if not lows_after:
                    return None
                l1_idx   = min(lows_after, key=lambda i: self._history[i].low)
                l1_price = self._history[l1_idx].low

                highs_after = [i for i in pivot_highs if i > l1_idx]
                if not highs_after:
                    return None
                h2_idx   = max(highs_after, key=lambda i: self._history[i].high)
                h2_price = self._history[h2_idx].high

                return self._assemble_down_channel(
                    h1_idx, self._carry_price,
                    l1_idx, l1_price,
                    h2_idx, h2_price,
                    n,
                )

        # 일반 모드: H1/L1/H2 모두 탐색
        if len(pivot_highs) < 2 or len(pivot_lows) < 1:
            return None

        l1_idx   = min(pivot_lows, key=lambda i: self._history[i].low)
        l1_price = self._history[l1_idx].low

        highs_before = [i for i in pivot_highs if i < l1_idx]
        if not highs_before:
            return None
        h1_idx   = max(highs_before, key=lambda i: self._history[i].high)
        h1_price = self._history[h1_idx].high

        highs_after = [i for i in pivot_highs if i > l1_idx]
        if not highs_after:
            return None
        h2_idx   = max(highs_after, key=lambda i: self._history[i].high)
        h2_price = self._history[h2_idx].high

        return self._assemble_down_channel(
            h1_idx, h1_price,
            l1_idx, l1_price,
            h2_idx, h2_price,
            n,
        )

    def _assemble_down_channel(
        self,
        h1_idx: int, h1_price: float,
        l1_idx: int, l1_price: float,
        h2_idx: int, h2_price: float,
        n: int,
    ) -> TrendChannel | None:
        """H1/L1/H2 좌표로 하락 채널을 조립."""
        slope = (h2_price - h1_price) / (h2_idx - h1_idx)
        if slope >= 0:
            return None

        upper_at_l1 = h1_price + slope * (l1_idx - h1_idx)
        channel_gap = upper_at_l1 - l1_price
        if channel_gap <= 0:
            return None

        for i in range(h1_idx, h2_idx + 1):
            upper_i = h1_price + slope * (i - h1_idx)
            c = self._history[i]
            if c.high > upper_i or c.low < upper_i - channel_gap:
                return None

        upper_now = h1_price + slope * (n - 1 - h1_idx)
        lower_now = upper_now - channel_gap

        return TrendChannel(
            direction   = "down",
            slope       = slope,
            lower_now   = lower_now,
            upper_now   = upper_now,
            channel_gap = channel_gap,
            l1_idx      = h1_idx,  l1_price = h1_price,
            l2_idx      = h2_idx,  l2_price = h2_price,
            h1_idx      = l1_idx,  h1_price = l1_price,
        )

    # ── carry H1 인덱스 탐색 ─────────────────────────────────────────────────

    def _find_carry_in_history(self) -> int | None:
        """_carry_ts와 일치하는 history 인덱스를 역방향으로 탐색."""
        for i in range(len(self._history) - 1, -1, -1):
            if self._history[i].timestamp == self._carry_ts:
                return i
        return None

    # ── 피벗 탐색 ────────────────────────────────────────────────────────────

    def _find_pivots(self, is_low: bool) -> list[int]:
        n         = len(self._history)
        win_start = max(0, n - self.window)
        k         = self.pivot_k
        pivots: list[int] = []

        for i in range(win_start, n - k):
            lo  = max(0,   i - k)
            hi  = min(n-1, i + k)
            ref = self._history[i].low if is_low else self._history[i].high

            is_pivot = True
            for j in range(lo, hi + 1):
                if j == i:
                    continue
                val = self._history[j].low if is_low else self._history[j].high
                if is_low and val <= ref:
                    is_pivot = False; break
                if not is_low and val >= ref:
                    is_pivot = False; break

            if is_pivot:
                pivots.append(i)

        return pivots

    def _no_signal(self) -> PatternResult:
        return PatternResult(detected=False, direction=None, strength=0.0, name=self.name)
