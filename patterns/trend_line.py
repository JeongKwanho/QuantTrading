from dataclasses import dataclass

from patterns.base import BasePattern, PatternResult
from strategies.base import MarketData


# ── 채널 데이터 ───────────────────────────────────────────────────────────────

@dataclass
class TrendChannel:
    """
    두 평행선 채널. 3개 피벗 포인트로 구성.

    direction = "up":
        lower  ─── L1 → L2  (최저 피벗 저점 2개)
        upper  ─ ─ 동일 기울기, H1 (최고 피벗 고점) 통과

    direction = "down":
        upper  ─── H1 → H2  (최고 피벗 고점 2개)
        lower  ─ ─ 동일 기울기, L1 (최저 피벗 저점) 통과

    필드 의미:
      "up"   → l1/l2 = 저점 포인트,  h1 = 고점 기준
      "down" → l1/l2 = 고점 포인트,  h1 = 저점 기준
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
    피벗 포인트로 상승/하락 채널을 각각 구성한다.

    uptrend_channel:   L1, L2 (최저 저점 2개)  + H1 (최고 고점)
    downtrend_channel: H1, H2 (최고 고점 2개)  + L1 (최저 저점)
    """

    def __init__(self, window: int = 50, pivot_k: int = 2, **kwargs) -> None:
        self.window  = window
        self.pivot_k = pivot_k
        self._history: list[MarketData] = []
        self.uptrend_channel:   TrendChannel | None = None
        self.downtrend_channel: TrendChannel | None = None

    @property
    def name(self) -> str:
        return "trend_line"

    # ── 메인 ─────────────────────────────────────────────────────────────────

    def evaluate(self, data: MarketData) -> PatternResult:
        self._history.append(data)
        if len(self._history) > self.window * 3:
            self._history = self._history[-self.window * 3:]

        n = len(self._history)
        if n < self.pivot_k * 2 + 2:
            self.uptrend_channel   = None
            self.downtrend_channel = None
            return self._no_signal()

        pivot_lows  = self._find_pivots(is_low=True)
        pivot_highs = self._find_pivots(is_low=False)

        self.uptrend_channel   = self._build_uptrend_channel(pivot_lows, pivot_highs, n)
        self.downtrend_channel = self._build_downtrend_channel(pivot_lows, pivot_highs, n)

        detected = self.uptrend_channel is not None or self.downtrend_channel is not None
        return PatternResult(
            detected  = detected,
            direction = None,   # TODO: 조건 결정 후 채울 것
            strength  = 0.0,    # TODO
            name      = self.name,
        )

    def reset(self) -> None:
        self._history.clear()
        self.uptrend_channel   = None
        self.downtrend_channel = None

    # ── 상승 채널 ────────────────────────────────────────────────────────────

    def _build_uptrend_channel(
        self,
        pivot_lows:  list[int],
        pivot_highs: list[int],
        n: int,
    ) -> TrendChannel | None:
        if len(pivot_lows) < 2 or len(pivot_highs) < 1:
            return None

        # H1: 가장 높은 고점
        h1_idx   = max(pivot_highs, key=lambda i: self._history[i].high)
        h1_price = self._history[h1_idx].high

        # L1: H1 앞쪽 피벗 저점 중 최저
        lows_before = [i for i in pivot_lows if i < h1_idx]
        if not lows_before:
            return None
        l1_idx   = min(lows_before, key=lambda i: self._history[i].low)
        l1_price = self._history[l1_idx].low

        # L2: H1 뒤쪽 피벗 저점 중 최저
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
        if len(pivot_highs) < 2 or len(pivot_lows) < 1:
            return None

        # L1: 가장 낮은 저점
        l1_idx   = min(pivot_lows, key=lambda i: self._history[i].low)
        l1_price = self._history[l1_idx].low

        # H1: L1 앞쪽 피벗 고점 중 최고
        highs_before = [i for i in pivot_highs if i < l1_idx]
        if not highs_before:
            return None
        h1_idx   = max(highs_before, key=lambda i: self._history[i].high)
        h1_price = self._history[h1_idx].high

        # H2: L1 뒤쪽 피벗 고점 중 최고
        highs_after = [i for i in pivot_highs if i > l1_idx]
        if not highs_after:
            return None
        h2_idx   = max(highs_after, key=lambda i: self._history[i].high)
        h2_price = self._history[h2_idx].high

        slope = (h2_price - h1_price) / (h2_idx - h1_idx)

        if slope >= 0:
            return None

        upper_at_l1 = h1_price + slope * (l1_idx - h1_idx)
        channel_gap = upper_at_l1 - l1_price

        for i in range(h1_idx, h2_idx + 1):
            upper_i = h1_price + slope * (i - h1_idx)
            c = self._history[i]
            if c.high > upper_i or c.low < upper_i - channel_gap:
                return None

        upper_now = h1_price + slope * (n - 1 - h1_idx)
        lower_now = upper_now - channel_gap

        # l1/l2 필드에 H1/H2 저장, h1 필드에 L1 저장
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

    # ── 피벗 탐색 ────────────────────────────────────────────────────────────

    def _find_pivots(self, is_low: bool) -> list[int]:
        n         = len(self._history)
        win_start = max(0, n - self.window)
        k         = self.pivot_k
        pivots: list[int] = []

        for i in range(win_start, n):
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
